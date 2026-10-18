"""J.A.R.V.I.S. 4-bit QLoRA Local & Cloud Fine-Tuning Pipeline.

Fine-tunes a local SLM (e.g. Qwen2.5-Coder-3B or Llama-3.2-3B) on J.A.R.V.I.S. SFT
datasets using 4-bit NormalFloat (NF4) quantization and Low-Rank Adaptation (LoRA).
Designed to fit within consumer GPU VRAM (4 GB NVIDIA RTX 3050 Laptop GPU).
Also generates 1-click Google Colab / Kaggle notebooks for free cloud training.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TRAINING_DIR = DATA_DIR / "training"
ALPACA_DATASET = TRAINING_DIR / "sft_alpaca.jsonl"
OUTPUT_MODEL_DIR = ROOT / "models" / "jarvis-lora-adapter"


def generate_colab_notebook(output_path: Path) -> None:
    """Generate a self-contained 1-click Google Colab notebook for free T4/A100 fine-tuning."""
    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# 🚀 J.A.R.V.I.S. Autonomous Brain Fine-Tuning Studio\n",
                "Train your customized J.A.R.V.I.S. model on Google Colab (Free T4 GPU) using Unsloth & QLoRA.\n",
                "Fast, 2x memory efficient, and exports directly to GGUF for Ollama!"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 1. Install Unsloth & SFT requirements\n",
                "!pip install --upgrade --no-cache-dir \"unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git\"\n",
                "!pip install --no-deps trl peft accelerate bitsandbytes"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 2. Load Base Model (Qwen2.5-Coder-3B or Llama-3.2-3B in 4-bit)\n",
                "from unsloth import FastLanguageModel\n",
                "import torch\n",
                "\n",
                "max_seq_length = 2048\n",
                "model, tokenizer = FastLanguageModel.from_pretrained(\n",
                "    model_name=\"unsloth/Qwen2.5-Coder-3B-Instruct\",\n",
                "    max_seq_length=max_seq_length,\n",
                "    load_in_4bit=True,\n",
                ")\n",
                "\n",
                "# 3. Add LoRA Adapters\n",
                "model = FastLanguageModel.get_peft_model(\n",
                "    model,\n",
                "    r=16,\n",
                "    target_modules=[\"q_proj\", \"k_proj\", \"v_proj\", \"o_proj\", \"gate_proj\", \"up_proj\", \"down_proj\"],\n",
                "    lora_alpha=16,\n",
                "    lora_dropout=0,\n",
                "    bias=\"none\",\n",
                "    use_gradient_checkpointing=\"unsloth\",\n",
                ")\n",
                "print(\"LoRA parameters initialized!\")"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 4. Upload your dataset or paste SFT records\n",
                "# Upload 'sft_alpaca.jsonl' from your Jarvis data/training/ folder\n",
                "from google.colab import files\n",
                "import json\n",
                "from datasets import Dataset\n",
                "\n",
                "uploaded = files.upload()\n",
                "filename = list(uploaded.keys())[0]\n",
                "\n",
                "data = []\n",
                "with open(filename, 'r', encoding='utf-8') as f:\n",
                "    for line in f:\n",
                "        if line.strip():\n",
                "            data.append(json.loads(line))\n",
                "\n",
                "dataset = Dataset.from_list(data)\n",
                "print(f\"Loaded {len(dataset)} training samples!\")"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 5. Execute Supervised Fine-Tuning (SFT)\n",
                "from trl import SFTTrainer\n",
                "from transformers import TrainingArguments\n",
                "\n",
                "trainer = SFTTrainer(\n",
                "    model=model,\n",
                "    tokenizer=tokenizer,\n",
                "    train_dataset=dataset,\n",
                "    dataset_text_field=\"output\",\n",
                "    max_seq_length=max_seq_length,\n",
                "    args=TrainingArguments(\n",
                "        per_device_train_batch_size=2,\n",
                "        gradient_accumulation_steps=4,\n",
                "        warmup_steps=5,\n",
                "        max_steps=60,\n",
                "        learning_rate=2e-4,\n",
                "        fp16=not torch.cuda.is_bf16_supported(),\n",
                "        bf16=torch.cuda.is_bf16_supported(),\n",
                "        logging_steps=1,\n",
                "        output_dir=\"jarvis_lora\",\n",
                "    ),\n",
                ")\n",
                "trainer.train()\n",
                "print(\"Training Complete!\")"
            ]
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "# 6. Export to 16-bit GGUF or Ollama Modelfile\n",
                "model.save_pretrained_gguf(\"jarvis_gguf\", tokenizer, quantization_method=\"q4_k_m\")\n",
                "# Download the GGUF model and drop it into Ollama:\n",
                "# ollama create jarvis-custom -f Modelfile\n",
                "print(\"Export complete!\")"
            ]
        }
    ]

    nb = {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": []},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(nb, indent=2), encoding="utf-8")
    print(f"[Fine-Tune] 1-Click Colab Notebook generated -> {output_path}")


def run_local_finetune(
    base_model: str = "Qwen/Qwen2.5-Coder-3B-Instruct",
    dataset_path: Path = ALPACA_DATASET,
    output_dir: Path = OUTPUT_MODEL_DIR,
    epochs: int = 3,
    batch_size: int = 1,
    lr: float = 2e-4,
) -> bool:
    """Run 4-bit local QLoRA training if PyTorch & CUDA are available."""
    print(f"\n========================================================")
    print(f"  J.A.R.V.I.S. LOCAL 4-BIT QLoRA FINE-TUNING ENGINE")
    print(f"========================================================")
    print(f"Base Model:       {base_model}")
    print(f"Dataset:          {dataset_path}")
    print(f"Output Adapter:   {output_dir}")
    print(f"Target VRAM:      ~3.2 GB (Fits 4GB RTX 3050 Laptop GPU)")
    print(f"--------------------------------------------------------")

    if not dataset_path.exists():
        print(f"[Error] Dataset not found at {dataset_path}. Run extract_training_dataset.py first!", file=sys.stderr)
        return False

    try:
        import torch
        print(f"[CUDA] PyTorch version: {torch.__version__}")
        print(f"[CUDA] CUDA Available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"[CUDA] Active Device: {torch.cuda.get_device_name(0)}")
            print(f"[CUDA] Total VRAM: {torch.cuda.get_device_properties(0).total_memory / (1024**3):.2f} GB")
    except ImportError:
        print("[Notice] PyTorch is not currently installed in this environment.")

    # Check for Hugging Face PEFT / TRL packages
    missing_pkgs = []
    for pkg in ["transformers", "peft", "trl", "bitsandbytes", "datasets"]:
        try:
            __import__(pkg)
        except ImportError:
            missing_pkgs.append(pkg)

    if missing_pkgs:
        print(f"[Notice] Deep training libraries missing: {', '.join(missing_pkgs)}")
        print("To install local training packages on Windows with CUDA:")
        print("  pip install torch --index-url https://download.pytorch.org/whl/cu121")
        print("  pip install transformers peft trl bitsandbytes datasets accelerate")
        print("\nAlternatively, use the 1-click Google Colab notebook generated at:")
        print(f"  {TRAINING_DIR / 'jarvis_finetune_colab.ipynb'}")
        return False

    print("[Fine-Tune] Initializing 4-bit Quantization Config (BitsAndBytes NF4)...")
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer
    from datasets import load_dataset

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    print(f"[Fine-Tune] Loading base model weights: {base_model}...")
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    print(f"[Fine-Tune] Loading training dataset: {dataset_path}...")
    dataset = load_dataset("json", data_files=str(dataset_path))["train"]

    def format_prompts(batch):
        formatted = []
        for inst, inp, outp in zip(batch["instruction"], batch["input"], batch["output"]):
            prompt = f"<|im_start|>system\n{ROOT.name} Executive Assistant<|im_end|>\n<|im_start|>user\n{inst}"
            if inp:
                prompt += f"\n\nContext:\n{inp}"
            prompt += f"<|im_end|>\n<|im_start|>assistant\n{outp}<|im_end|>"
            formatted.append(prompt)
        return {"text": formatted}

    dataset = dataset.map(format_prompts, batched=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        learning_rate=lr,
        logging_steps=10,
        num_train_epochs=epochs,
        fp16=True,
        optim="paged_adamw_8bit",
        save_strategy="epoch",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=1024,
        tokenizer=tokenizer,
        args=training_args,
    )

    print("[Fine-Tune] Starting training epochs...")
    trainer.train()
    output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    print(f"[Fine-Tune] Model weights and LoRA adapter saved to {output_dir}!")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. LoRA Fine-Tuning Runner")
    parser.add_argument("--colab-only", action="store_true", help="Generate the Google Colab notebook only")
    parser.add_argument("--base-model", type=str, default="Qwen/Qwen2.5-Coder-3B-Instruct")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--dataset", type=Path, default=ALPACA_DATASET)
    args = parser.parse_args()

    # Always generate the Colab notebook so the user has immediate access
    colab_nb_path = TRAINING_DIR / "jarvis_finetune_colab.ipynb"
    generate_colab_notebook(colab_nb_path)

    if args.colab_only:
        return

    # Attempt local run
    success = run_local_finetune(
        base_model=args.base_model,
        dataset_path=args.dataset,
        epochs=args.epochs,
    )
    if not success:
        print("\n[Tip] You can upload 'jarvis_finetune_colab.ipynb' and 'sft_alpaca.jsonl' directly to Google Colab to train for free in 5 minutes!")


if __name__ == "__main__":
    main()
