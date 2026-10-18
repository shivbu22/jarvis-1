"""J.A.R.V.I.S. Master Training Suite Orchestrator.

Unifies all 6 learning and adaptation modalities:
1. Knowledge Vault & SQLite Semantic Ingestion
2. Historical SFT Dataset Extraction (Alpaca & ShareGPT)
3. Ollama Ultimate Modelfile Compilation
4. 4-bit QLoRA Fine-Tuning & Colab Cloud Studio
5. Biometric Speaker Voiceprint Enrollment
6. Neural Voice Cloning & Style Conditioning
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title.upper()}")
    print("=" * 64)


def run_cmd(args: list[str], desc: str) -> bool:
    banner(desc)
    res = subprocess.run(args, cwd=ROOT)
    return res.returncode == 0


def check_status() -> None:
    banner("J.A.R.V.I.S. Training & Cognitive Systems Status")

    # 1. Dataset Status
    training_dir = ROOT / "data" / "training"
    alpaca = training_dir / "sft_alpaca.jsonl"
    colab = training_dir / "jarvis_finetune_colab.ipynb"
    manifest = training_dir / "dataset_manifest.json"

    samples_count = 0
    if alpaca.exists():
        with open(alpaca, "r", encoding="utf-8") as f:
            samples_count = sum(1 for line in f if line.strip())

    print(f"[1. SFT Dataset]        : {'READY' if samples_count > 0 else 'EMPTY'} ({samples_count} samples in {alpaca.name if alpaca.exists() else 'N/A'})")
    print(f"                        : Colab Studio -> {'PRESENT' if colab.exists() else 'NOT GENERATED'}")

    # 2. Knowledge Vault
    vault_dir = ROOT / "jarvis brain"
    vault_notes = len(list(vault_dir.rglob("*.md"))) if vault_dir.exists() else 0
    print(f"[2. Knowledge Vault]    : ACTIVE ({vault_notes} markdown documents indexed)")

    # 3. Speaker Biometrics
    speakers_dir = ROOT / "data" / "speakers"
    meta_file = speakers_dir / "speakers_meta.json"
    speaker_count = 0
    if meta_file.exists():
        try:
            import json
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            speaker_count = len(meta)
        except Exception:
            pass
    print(f"[3. Speaker Biometrics] : {speaker_count} Profile(s) Enrolled ({speakers_dir})")

    # 4. Neural Voice
    voice_ref = ROOT / "voices" / "jarvis-5s.wav"
    conds = ROOT / "voices" / "jarvis-conds.pt"
    print(f"[4. Neural Voice Cloner]: Reference WAV: {'PRESENT' if voice_ref.exists() else 'MISSING'} | Style Latents: {'BUILT' if conds.exists() else 'STANDBY (Edge British Active)'}")

    # 5. Local Models (Ollama)
    print("[5. Local LLM Models]   : Checking Ollama models...")
    try:
        ollama_out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        if ollama_out.returncode == 0:
            lines = [l for l in ollama_out.stdout.strip().split("\n") if l.strip()]
            for l in lines[:5]:
                print(f"      {l}")
        else:
            print("      Ollama daemon not responding.")
    except Exception:
        print("      Ollama command not in PATH or not installed.")

    print("=" * 64)


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. Master Training Suite")
    parser.add_argument("--all", action="store_true", help="Execute complete automated training pipeline")
    parser.add_argument("--status", action="store_true", help="Display status of all training systems")
    parser.add_argument("--dataset", action="store_true", help="Extract and compile SFT datasets from logs")
    parser.add_argument("--knowledge", action="store_true", help="Ingest knowledge into vault and memory")
    parser.add_argument("--speaker", action="store_true", help="Enroll or list speaker voiceprints")
    parser.add_argument("--voice", action="store_true", help="Build neural voice conditioning latents")
    parser.add_argument("--modelfile", action="store_true", help="Recompile Ollama jarvis-ultimate model")
    parser.add_argument("--finetune", action="store_true", help="Run local/cloud LoRA fine-tuning")
    parser.add_argument("--path", type=Path, help="Target path for knowledge ingestion")
    parser.add_argument("--name", type=str, default="Ahiv", help="Speaker name for voiceprint enrollment")
    args = parser.parse_args()

    if len(sys.argv) == 1 or args.status:
        check_status()
        return

    successes = []

    if args.dataset or args.all:
        ok = run_cmd([PY, "scripts/extract_training_dataset.py"], "Step 1: Extract SFT Training Dataset")
        successes.append(("Dataset Extraction", ok))

    if args.knowledge or args.all:
        k_args = [PY, "scripts/ingest_knowledge.py"]
        if args.path:
            k_args.extend(["--path", str(args.path)])
        ok = run_cmd(k_args, "Step 2: Ingest Knowledge Vault & SQLite Index")
        successes.append(("Knowledge Ingestion", ok))

    if args.speaker or args.all:
        ok = run_cmd([PY, "scripts/enroll_speaker_cli.py", "--name", args.name, "--description", "Primary Master"], "Step 3: Biometric Speaker Voiceprint Enrollment")
        successes.append(("Speaker Enrollment", ok))

    if args.modelfile or args.all:
        banner("Step 4: Ollama jarvis-ultimate Compilation")
        mf_path = ROOT / "config" / "ollama" / "Modelfile.jarvis"
        res = subprocess.run(["ollama", "create", "jarvis-ultimate:latest", "-f", str(mf_path)])
        successes.append(("Ollama jarvis-ultimate", res.returncode == 0))

    if args.voice or args.all:
        banner("Step 5: Neural Voice Pipeline Verification")
        res = subprocess.run([PY, "scripts/build_jarvis_voice.py"])
        successes.append(("Voice Pipeline Check", res.returncode == 0))

    if args.finetune:
        ok = run_cmd([PY, "scripts/finetune_lora.py"], "Step 6: 4-bit QLoRA Fine-Tuning Runner")
        successes.append(("Fine-Tuning", ok))

    banner("J.A.R.V.I.S. Training Suite Execution Summary")
    for name, ok in successes:
        status = "[PASSED]" if ok else "[ATTENTION REQUIRED]"
        print(f"  {status:<22} : {name}")
    print("=" * 64)


if __name__ == "__main__":
    main()
