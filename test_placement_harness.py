"""No-GPU tests for placement_harness: domains, resume isolation, recovery, prompts, label versions.

run() is exercised for real with fake torch/transformers modules, so these tests check the
flow and the saved files. They say nothing about how the real models behave.
"""
import contextlib
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import placement_harness as n

HERE = Path(__file__).resolve().parent
FIELDS = ["idx", "user_prompt", "input_decision", "policy_rules", "policy_sha256"]
TARGET = "org/Fake-Target"


class Row(list):
    def __getitem__(self, k):
        r = super().__getitem__(k)
        return Row(r) if isinstance(k, slice) else r

    def tolist(self):
        return list(self)


class Batch:
    def __init__(self, ids):
        self.row = Row(ids)
        self.shape = (1, len(ids))

    def __getitem__(self, k):
        return self.row


class Enc(dict):
    def to(self, device):
        return self


class Prob:
    def __getitem__(self, k):
        return self

    def float(self):
        return self

    def item(self):
        return 0.5


class Tok:
    eos_token_id = 0
    words = {1: "safe", 2: "unsafe", 3: "\n\n", 10: "fake answer"}

    def __call__(self, text, return_tensors=None, add_special_tokens=True):
        Fakes.last_text = text
        return Enc(input_ids=Batch([5] * 4))

    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
        Fakes.messages.append(messages)
        return json.dumps(messages)

    def encode(self, text, add_special_tokens=False):
        return [1] if text == "safe" else [2]

    def decode(self, ids, skip_special_tokens=False):
        return "".join(self.words.get(i, "") for i in ids)


class Target:
    generation_config = types.SimpleNamespace(eos_token_id=[0, 9])

    def eval(self):
        return self

    def generate(self, input_ids=None, **kw):
        Fakes.tick("gen")
        return Batch(list(input_ids.row) + ([10, 9] if Fakes.ends_cleanly else [10]))


class Guard(Target):
    def generate(self, input_ids=None, **kw):
        Fakes.tick("guard")
        return types.SimpleNamespace(sequences=[Row(list(input_ids.row) + [3, 1])], scores=[Prob(), Prob()])


class Fakes:
    """Counts model calls; raises after fail_after calls to imitate a job being killed."""
    calls = {"gen": 0, "guard": 0}
    messages = []
    fail_after = None
    last_text = ""
    ends_cleanly = True
    revisions = []

    @classmethod
    def reset(cls, fail_after=None):
        cls.calls = {"gen": 0, "guard": 0}
        cls.messages = []
        cls.fail_after = fail_after
        cls.revisions = []

    @classmethod
    def tick(cls, kind):
        cls.calls[kind] += 1
        if cls.fail_after and cls.fail_after.get(kind) == cls.calls[kind]:
            raise KeyboardInterrupt(f"killed during {kind}")


def fake_modules():
    def from_model(name, **kw):
        Fakes.revisions.append((name, kw.get("revision")))
        return Guard() if name == n.GUARD else Target()
    torch = types.SimpleNamespace(
        bfloat16="bf16", no_grad=contextlib.nullcontext, softmax=lambda x, dim: Prob(),
        cuda=types.SimpleNamespace(empty_cache=lambda: None))
    transformers = types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda name, **kw: Tok()),
        AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=from_model))
    return {"torch": torch, "transformers": transformers}


def write_rows(path, prompts, sha="s" * 64):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for i, p in enumerate(prompts):
            w.writerow(dict(idx=i, user_prompt=p, input_decision="allow", policy_rules="A1", policy_sha256=sha))


class RunCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.data = self.home / "labels.csv"
        write_rows(self.data, ["first?", "second?", "third?"])
        hard = self.home / "hard.jsonl"
        hard.write_text("".join(json.dumps(dict(question=f"benign {i}", original_question=f"harm {i}")) + "\n"
                                for i in range(3)), encoding="utf-8")
        for p in (mock.patch.object(n, "HOME", self.home), mock.patch.object(n, "FIN_DATA", self.data),
                  mock.patch.object(n, "HARD", hard), mock.patch.object(n, "revision", lambda name: "rev"),
                  mock.patch.dict(sys.modules, fake_modules())):
            p.start()
            self.addCleanup(p.stop)

    def run_harness(self, fail_after=None, **env):
        Fakes.reset(fail_after)
        env = dict(dict(TARGET_MODEL=TARGET, DOMAIN="financial"), **env)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {k: str(v) for k, v in env.items() if v is not None}), \
                contextlib.redirect_stdout(out):
            for k in [k for k, v in env.items() if v is None]:
                __import__("os").environ.pop(k, None)
            n.run()
        return out.getvalue()

    def files(self, pattern):
        return sorted(self.home.glob(pattern))

    def atoms(self):
        found = self.files("placement_atoms_*.json")
        self.assertEqual(len(found), 1, found)
        return json.loads(found[0].read_text(encoding="utf-8"))


class DomainSelection(RunCase):
    def test_default_is_medical(self):
        self.run_harness(DOMAIN=None, N=1)
        got = self.atoms()
        self.assertEqual(got["meta"]["domain"], "medical")
        self.assertEqual(got["meta"]["policy_sha256"], n.load_policy("medical")[1])
        self.assertEqual([a["prompt"] for a in got["atoms"]], ["benign 0", "harm 0"])

    def test_financial_uses_its_policy_data_and_prompts(self):
        self.run_harness()
        got = self.atoms()
        self.assertEqual(got["meta"]["domain"], "financial")
        self.assertEqual(got["meta"]["policy_sha256"], n.load_policy("financial")[1])
        self.assertEqual([a["prompt"] for a in got["atoms"]], ["first?", "second?", "third?"])
        system = [m[0]["content"] for m in Fakes.messages if m[0]["role"] == "system"]
        self.assertEqual(len(system), 3)
        self.assertTrue(all(s.startswith("You are a financial-education assistant") for s in system))
        for arm_text in got["atoms"][0]["prompts"]["input"]["policy"], got["atoms"][0]["prompts"]["output"]["1"]["policy"]:
            self.assertIn("S6: " + n.FINANCIAL_CATS["S6"], arm_text)
            self.assertNotIn("personal health", arm_text)

    def test_unknown_domain_fails_before_any_output(self):
        with self.assertRaises(KeyError):
            self.run_harness(DOMAIN="legal")
        self.assertEqual(self.files("placement_*"), [])

    def test_stale_labels_warn(self):
        self.assertIn("WARNING: labels use policy sha", self.run_harness())


class ResumeIsolation(RunCase):
    def assert_starts_fresh(self, **env):
        self.run_harness(**env)
        self.assertEqual(Fakes.calls["gen"], 6, "changed setup must not reuse earlier answers")

    def setUp(self):
        super().setUp()
        self.run_harness()
        self.assertEqual(Fakes.calls, {"gen": 6, "guard": 27})

    def test_same_setup_reuses_everything(self):
        self.run_harness()
        self.assertEqual(Fakes.calls, {"gen": 0, "guard": 0})

    def test_other_domain(self):
        self.run_harness(DOMAIN="medical", N=1)
        self.assertEqual(Fakes.calls["gen"], 4)

    def test_changed_policy(self):
        with mock.patch.object(n, "load_policy", lambda d="medical": ("other text", "f" * 64)):
            self.assert_starts_fresh()

    def test_changed_prompts(self):
        write_rows(self.data, ["first?", "EDITED?", "third?"])
        self.assert_starts_fresh()

    def test_changed_token_cap(self):
        with mock.patch.object(n, "MAX_NEW", 999):
            self.assert_starts_fresh()

    def test_changed_target_revision(self):
        self.assert_starts_fresh(TARGET_REVISION="abc123")

    def test_changed_category_text_is_not_covered_by_policy_sha(self):
        with mock.patch.dict(n.FINANCIAL_CATS, S6="Edited S6."):
            self.assert_starts_fresh()

    def test_changed_guard_revision(self):
        with mock.patch.object(n, "GUARD_REV", "other"):
            self.assert_starts_fresh()

    def test_changed_model_task(self):
        with mock.patch.dict(n.DOMAINS["financial"], task="Edited task."):
            self.assert_starts_fresh()


class InterruptRecovery(RunCase):
    def gen_file(self):
        return self.files("placement_gen_*.jsonl")[0]

    def guard_file(self):
        return self.files("placement_guard_*.jsonl")[0]

    def idxs(self, path):
        return [json.loads(l)["idx"] for l in path.read_text(encoding="utf-8").splitlines()]

    def test_killed_during_generation(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_harness(fail_after={"gen": 4})  # item 0 done, item 1 half done
        self.assertEqual(self.idxs(self.gen_file()), [0])
        self.run_harness()
        self.assertEqual(Fakes.calls["gen"], 4)
        self.assertEqual(self.idxs(self.gen_file()), [0, 1, 2])
        self.assertEqual(len(self.atoms()["atoms"]), 3)

    def test_killed_during_guard(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_harness(fail_after={"guard": 12})  # item 0 done, item 1 partly done
        self.assertEqual(self.idxs(self.guard_file()), [0])
        self.run_harness()
        self.assertEqual(Fakes.calls, {"gen": 0, "guard": 18})
        self.assertEqual(self.idxs(self.guard_file()), [0, 1, 2])

    def test_line_cut_off_by_a_reboot_is_rerun(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_harness(fail_after={"gen": 4})
        with open(self.gen_file(), "a", encoding="utf-8") as f:
            f.write('{"idx": 1, "resp": {"0": "half an ans')
        self.run_harness()
        self.assertEqual(self.idxs(self.gen_file()), [0, 1, 2])

    def test_bad_line_inside_file_stops_the_run(self):
        p = self.home / "x.jsonl"
        p.write_text('{"idx": 0}\nnot json\n{"idx": 2}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"x\.jsonl:2"):
            n._read_jsonl(p)

    def test_repeated_idx_stops_the_run(self):
        p = self.home / "x.jsonl"
        p.write_text('{"idx": 0}\n{"idx": 0}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repeated idx 0"):
            n._read_jsonl(p)

    def test_model_text_with_unicode_line_separator_survives(self):
        p = self.home / "x.jsonl"
        p.write_text(json.dumps(dict(idx=0, resp="a b"), ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(n._read_jsonl(p)[0]["resp"], "a b")

    def test_missing_file_is_empty(self):
        self.assertEqual(n._read_jsonl(self.home / "none.jsonl"), {})


class PromptRegression(unittest.TestCase):
    def test_selfcheck(self):
        with contextlib.redirect_stdout(io.StringIO()):
            n.selfcheck()

    def test_rendered_prompts_for_both_domains(self):
        for dm in n.DOMAINS:
            text, _ = n.load_policy(dm)
            cats = n.DOMAINS[dm]["cats"]
            for response in (None, "RESP"):
                d = n.build_guard_prompt("Q?", response, "default", text, dm)
                p = n.build_guard_prompt("Q?", response, "policy", text, dm)
                for prompt in (d, p):
                    self.assertIsNone(re.search(r"\{[a-zA-Z_]+\}|<\?claude|TODO", prompt), dm)
                    self.assertEqual(re.findall(r"^S\d+:", prompt, re.M), [f"S{i}:" for i in range(1, 15)])
                self.assertIn(f"S6: {cats['S6']}\n", p)
                self.assertIn(f"S11: {cats['S11']}\n", p)
                self.assertIn(f"<BEGIN DEPLOYMENT POLICY>\n{text}\n<END DEPLOYMENT POLICY>", p)
                self.assertIn("S6: Specialized Advice.\n", d)
                self.assertIn("S11: Self-Harm.\n", d)
                self.assertNotIn("DEPLOYMENT POLICY", d)
                c = n.build_guard_prompt("Q?", response, "compact", text, dm)
                self.assertEqual(c, p.replace(f"<BEGIN DEPLOYMENT POLICY>\n{text}\n<END DEPLOYMENT POLICY>\n\n", ""))
                self.assertIn(f"S6: {cats['S6']}\n", c)

    def test_default_arm_does_not_depend_on_domain(self):
        self.assertEqual(n.build_guard_prompt("Q", "R", "default", "x", "medical"),
                         n.build_guard_prompt("Q", "R", "default", "x", "financial"))

    def test_agreed_category_text(self):
        self.assertEqual(n.MEDICAL_CATS["S11"], n.FINANCIAL_CATS["S11"])
        self.assertNotIn("D1-D6", n.FINANCIAL_CATS["S6"])
        self.assertIn("insider trading", n.FINANCIAL_CATS["S6"])
        self.assertIn("medical records", n.MEDICAL_CATS["S6"])


class CompactArm(RunCase):
    def test_design_has_eighteen_guarded_configs_and_two_references(self):
        guarded = [c for c in n.CONFIGS if not c.startswith("G0")]
        self.assertEqual(len(guarded), 18)
        self.assertEqual(len(set(n.CONFIGS)), 20)
        self.assertIn("M1I2O2", guarded)

    def test_compose_reads_the_arm_each_switch_names(self):
        safe, unsafe = dict(verdict="safe"), dict(verdict="unsafe")
        atom = dict(tr={0: False, 1: False},
                    input=dict(default=safe, policy=safe, compact=unsafe),
                    output={m: dict(default=safe, policy=unsafe, compact=safe) for m in (0, 1)})
        self.assertEqual(n.compose(atom, "M0I2O0")["stop"], "input")
        self.assertEqual(n.compose(atom, "M0I0O1")["stop"], "output")
        self.assertEqual(n.compose(atom, "M0I0O2")["stop"], "none")

    def test_run_saves_all_three_arms_and_all_configs(self):
        self.run_harness()
        atom = self.atoms()["atoms"][0]
        self.assertEqual(set(atom["input"]), set(n.ARMS))
        self.assertEqual(set(atom["output"]["1"]), set(n.ARMS))
        self.assertEqual(set(atom["prompts"]["input"]), set(n.ARMS))
        self.assertNotIn("DEPLOYMENT POLICY", atom["prompts"]["input"]["compact"])
        records = json.loads(self.files("placement_results_*.json")[0].read_text(encoding="utf-8"))
        self.assertEqual({r["config"] for r in records}, set(n.CONFIGS))


class FrozenSetup(RunCase):
    def test_token_cap(self):
        self.assertEqual(n.MAX_NEW, 800)
        self.assertEqual(n.GEN_SETTINGS["max_new_tokens"], 800)

    def test_target_revision_defaults_to_the_pin(self):
        for name, rev in n.TARGET_REVS.items():
            self.assertRegex(rev, r"^[0-9a-f]{40}$", name)
        self.assertEqual(set(n.TARGET_REVS), set(n.TARGETS))
        target = n.TARGETS[0]
        self.run_harness(TARGET_MODEL=target)
        self.assertEqual(self.atoms()["meta"]["target_revision"], n.TARGET_REVS[target])
        self.assertIn((target, n.TARGET_REVS[target]), Fakes.revisions)
        self.assertIn((n.GUARD, n.GUARD_REV), Fakes.revisions)

    def test_stop_reason_and_length_are_saved(self):
        self.run_harness()
        a = self.atoms()["atoms"][0]
        self.assertEqual(a["stop_reason"], {"0": "eos", "1": "eos"})
        self.assertEqual(a["truncated"], {"0": False, "1": False})
        self.assertEqual(a["n_new"], {"0": 2, "1": 2})

    def test_answer_without_eos_counts_as_truncated(self):
        with mock.patch.object(Fakes, "ends_cleanly", False):
            self.run_harness()
        a = self.atoms()["atoms"][0]
        self.assertEqual(a["stop_reason"], {"0": "max_new", "1": "max_new"})
        self.assertEqual(a["truncated"], {"0": True, "1": True})


def policy_section_sha(path):
    s = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    a = re.search(r"^## Policy text.*\n", s, re.M).end()
    b = re.search(r"^## Where each rule is checked", s, re.M).start()
    return hashlib.sha256(s[a:b].strip().encode("utf-8")).hexdigest()


class LabelsAndVersions(unittest.TestCase):
    TABLES = {"medical": ("medical_policy_review_25.csv", "prompt", "A_input_decision"),
              "financial": ("data/financial_policy_review_first25_only.csv", "user_prompt", "input_decision")}

    def rows(self, dm):
        with open(HERE / self.TABLES[dm][0], encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def test_tables_are_well_formed(self):
        for dm, (_, prompt, decision) in self.TABLES.items():
            rows = self.rows(dm)
            self.assertEqual(len({r["idx"] for r in rows}), len(rows), dm)
            for r in rows:
                self.assertTrue(r[prompt].strip(), (dm, r["idx"]))
                self.assertIn(r[decision].strip(), {"allow", "block", "unclear"}, (dm, r["idx"]))

    def test_every_label_hash_has_a_matching_snapshot(self):
        for dm in self.TABLES:
            rows = self.rows(dm)
            for sha in {r["policy_sha256"] for r in rows} | {r["review_policy_sha256"] for r in rows}:
                path = HERE / f"{dm}_policy_review_policy_{sha[:8]}.md"
                self.assertTrue(path.exists(), path.name)
                self.assertEqual(policy_section_sha(path), sha, path.name)

    def test_review_columns_point_at_the_current_policy(self):
        for dm in self.TABLES:
            self.assertEqual({r["review_policy_sha256"] for r in self.rows(dm)}, {n.load_policy(dm)[1]})

    def test_financial_items_rejects_repeated_idx(self):
        row = dict(idx="1", user_prompt="Q", input_decision="allow", policy_rules="", policy_sha256="s")
        with self.assertRaisesRegex(ValueError, "repeated idx 1"):
            n.financial_items([row, row])

    def test_blank_table_for_annotator_b_hides_earlier_judgements(self):
        with open(HERE / "medical_policy_review_B_blank.csv", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(reader.fieldnames, ["idx", "prompt", "policy_sha256", "B_input_decision", "B_policy_rules",
                                             "B_response_refused", "B_response_compliant", "B_meets_minimum", "B_notes"])
        self.assertEqual([r["prompt"] for r in rows], [r["prompt"] for r in self.rows("medical")])
        self.assertEqual({r["policy_sha256"] for r in rows}, {n.load_policy("medical")[1]})
        self.assertFalse(any(v.strip() for r in rows for k, v in r.items() if k.startswith("B_")))


if __name__ == "__main__":
    unittest.main()
