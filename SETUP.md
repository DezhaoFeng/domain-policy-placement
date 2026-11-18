# Setup: running the experiments on SFU CSIL GPU workstations

Everything here is inference (no training). The answering models and the guard run locally on one GPU; the judge calls the DeepSeek API.

## 1. Connect

- Connect to the **SFU VPN** first. If every host times out, the VPN has dropped.
- SSH port is **24**: `ssh -p 24 <sfu_id>@secb1010-c12.csil.sfu.ca`
- Use an **RTX 4090 (24 GB)** host: `secb1010-{a12,b12,c01,c12,d11}`, `secb1013-{a01,a12,b01,b12,c01,c12}`. Check with `nvidia-smi`; pick another host if one is busy or offline.
- Your home directory is shared (NFS) across all workstations, so the venv, models and outputs are set up once.
- Workstations **reboot around 3:00 am Pacific**. Run long jobs with `nohup` and save outputs under your home directory.

Optional passwordless login (from your own machine): `ssh-copy-id -p 24 <sfu_id>@secb1010-c12.csil.sfu.ca` (on Windows, append `~/.ssh/id_ed25519.pub` to `~/.ssh/authorized_keys` on the host instead).

## 2. Python environment

```bash
git clone https://github.com/DezhaoFeng/domain-policy-placement.git ~/domain-policy-placement
cd ~/domain-policy-placement
python3 -m venv ~/g400_venv          # tested with Python 3.12 and 3.13
~/g400_venv/bin/pip install -r requirements.txt
~/g400_venv/bin/python placement_harness.py --selfcheck   # no GPU, no models
```

The pins in `requirements.txt` matter; see the comments there.

## 3. Models (Hugging Face)

| Role | Model | Revision |
|---|---|---|
| Input and output guard | `openai/gpt-oss-safeguard-20b` (~14 GB on GPU) | `8a11e17b25c973a24099d4016bf2e17dd7ec1574` |
| Answering model | `Qwen/Qwen2.5-7B-Instruct` | `a09a35458c702b33eeacc393d103063234e8bc28` |
| Answering model | `meta-llama/Llama-3.1-8B-Instruct` (**gated**) | `0e9e39f249a16976918f6564b8830bc894c89659` |
| Guard screening only | `meta-llama/Llama-Guard-3-8B` (**gated**), `Qwen/Qwen3Guard-Gen-8B` | see `guard_screen.py` |

For the gated Llama models, accept the licence on the model page, create a read token, then run `~/g400_venv/bin/hf auth login` once. Models download to `~/.cache/huggingface` on first use (about 60 GB in total; check your home quota). Revisions are pinned in `placement_harness.py`.

A 24 GB card cannot hold two 8B models at once; the harness loads the answering model and the guard in separate stages.

## 4. Data

- Financial: `data/financial_policy_review_first25_only.csv` is in the repo.
- Medical: the harness reads Health-ORSC-Bench from `~/orr_spike/Health-ORSC-Bench`:
  ```bash
  mkdir -p ~/orr_spike && git clone https://github.com/ZhihaoZhang97/Health-ORSC-Bench.git ~/orr_spike/Health-ORSC-Bench
  ```
- Policies: `policies/medical.md`, `policies/financial.md` (each run records the policy hash).

## 5. Run

```bash
cd ~/domain-policy-placement
PY=~/g400_venv/bin/python
$PY placement_harness.py --check-template            # tokenizers only
TARGET_MODEL=Qwen/Qwen2.5-7B-Instruct N=15 nohup $PY placement_harness.py --run > run.log 2>&1 &
DOMAIN=financial TARGET_MODEL=meta-llama/Llama-3.1-8B-Instruct nohup $PY placement_harness.py --run > run_fin.log 2>&1 &
$PY guard_screen.py safeguard                        # or llamaguard / qwen3guard
```

Runs are resumable: rerunning the same command continues where it stopped. Outputs are written to `~/orr_spike/` and stay **out of this public repo** (they contain raw model answers to harmful requests); share them privately.

Judge (any machine, standard library only): `DEEPSEEK_API_KEY=... python judge.py ATOMS.json --minimum MIN.csv`. Never commit the API key.

## Known pitfalls

- Don't stop jobs with `pkill -f <script>` over SSH: the pattern also matches your own SSH command. Save the PID (`echo $! > run.pid`) and use `kill $(cat run.pid)`.
- transformers 5.x: `apply_chat_template(..., return_tensors="pt")` returns a `BatchEncoding`; the scripts already handle this.
