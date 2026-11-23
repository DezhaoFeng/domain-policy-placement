"""Draw test-set candidates (2026-11-23 plan: 10 pairs per domain after review; oversampled to 14).

Medical: Health-ORSC-Bench hard-1k pairs (benign `question` + its harmful seed `original_question`),
2 per classification. Financial: FinSafetyBench English harmful questions, 1 per category; benign
twins are written separately and reviewed by hand. Anything already used in development (review
tables, S6 annotation, the 15-pair pilot) is excluded, together with every request sharing its
harmful seed, so development and test stay separated by template family.

Usage: python build_testset_candidates.py ORSC_HARD.jsonl FinsafetyBenchmark.csv OUT.csv
"""
import csv, json, random, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED, MED_PER_CLASS, FIN_MAX_CHARS = 2026, 2, 600


def dev_prompts():
    seen = set()
    for name, col in (("medical_policy_review_25.csv", "prompt"), ("s6_annotation.csv", "prompt")):
        p = HERE / name
        if p.exists():  # s6_annotation.csv is local only (gitignored)
            seen |= {r[col].strip() for r in csv.DictReader(open(p, encoding="utf-8-sig"))}
    return seen


def pilot_seeds(rows):
    """Harmful seeds of the earlier 15-pair pilot (old placement_harness.build_items, seed 0)."""
    rows = rows[:]
    random.Random(0).shuffle(rows)
    seeds = []
    for r in rows:
        oq = r.get("original_question")
        if oq and oq not in seeds:
            seeds.append(oq)
        if len(seeds) == 15:
            break
    return set(seeds)


def medical(rows, used):
    banned = pilot_seeds(rows) | {r["original_question"] for r in rows if r["question"].strip() in used}
    by_class = defaultdict(list)
    for r in rows:
        oq = r.get("original_question")
        if oq and oq not in banned and r["question"].strip() not in used:
            by_class[r["classification"]].append(r)
    rng, out = random.Random(SEED), []
    for cls in sorted(by_class):
        picked, seeds = [], set()
        for r in rng.sample(by_class[cls], len(by_class[cls])):
            if r["original_question"] not in seeds:  # one pair per seed
                seeds.add(r["original_question"]); picked.append(r)
            if len(picked) == MED_PER_CLASS:
                break
        for r in picked:
            pid = f"M{len(out) // 2:02d}"
            out.append(dict(domain="medical", pair_id=pid, kind="benign", category=cls, source=f"Health-ORSC hard id {r['id']}", prompt=r["question"]))
            out.append(dict(domain="medical", pair_id=pid, kind="harmful", category=cls, source=f"Health-ORSC hard id {r['id']} seed", prompt=r["original_question"]))
    return out


def financial(rows):
    by_cat = defaultdict(list)
    for r in rows:
        q = r["question_en"].strip()
        if q and len(q) <= FIN_MAX_CHARS:
            by_cat[r["category_en"].strip().lower()].append(r)
    rng, out = random.Random(SEED), []
    for cat in sorted(by_cat):
        r = rng.choice(by_cat[cat])
        pid = f"F{len(out) // 2:02d}"
        out.append(dict(domain="financial", pair_id=pid, kind="harmful", category=r["category_en"], source=f"FinSafetyBench id {r['id']} ({r['source']})", prompt=r["question_en"].strip()))
        out.append(dict(domain="financial", pair_id=pid, kind="benign", category=r["category_en"], source="benign twin (to write)", prompt=""))
    return out


def selfcheck():
    rows = [dict(id=str(i), question=f"q{i}", original_question=f"s{i % 40}", classification=f"c{i % 3}") for i in range(200)]
    used = {"q1"}
    med = medical(rows, used)
    seeds = [m["prompt"] for m in med if m["kind"] == "harmful"]
    assert len(med) == 2 * 3 * MED_PER_CLASS and len(set(seeds)) == len(seeds)
    assert "s1" not in seeds and not set(seeds) & pilot_seeds(rows)
    assert med == medical(rows, used)  # deterministic
    print("selfcheck OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        selfcheck(); sys.exit()
    orsc, fsb, out = sys.argv[1:4]
    med = medical([json.loads(l) for l in open(orsc, encoding="utf-8")], dev_prompts())
    fin = financial(list(csv.DictReader(open(fsb, encoding="utf-8-sig"))))
    cols = ["domain", "pair_id", "kind", "category", "source", "prompt", "input_decision", "min_acceptable_answer", "keep", "notes"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, restval="")
        w.writeheader(); w.writerows(med + fin)
    print(f"wrote {len(med)} medical + {len(fin)} financial rows to {out}")
