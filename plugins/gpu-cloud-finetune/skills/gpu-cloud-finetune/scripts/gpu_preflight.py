#!/usr/bin/env python3
"""
GPU 平台資格前置檢查（GPU preflight）

在租來的機器上第一件事就跑這支：在下載 31B 模型（約 20GB）之前，先確認這張卡
的 compute capability 有可用的 kernel。Blackwell（sm_120）需要 CUDA 12.8+ 編譯的
PyTorch；舊 wheel 會在實際運算時才炸 "no kernel image is available"，光看
torch.cuda.is_available() 是 True 會被騙。

用法：
  python gpu_preflight.py            # 全套檢查
  python gpu_preflight.py --quick    # 跳過 bitsandbytes / unsloth（只驗 torch 核心）

退出碼：0＝全過可繼續；1＝有 FAIL，停手換 image 或裝對版本的 torch。
"""

import argparse
import subprocess
import sys
import traceback
from typing import Callable

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}", flush=True)


def check(name: str, fn: Callable[[], str], fatal: bool = True) -> None:
    """跑一項檢查；例外一律記為 FAIL（或 WARN），不讓錯誤被吞掉。"""
    try:
        record(name, PASS, fn())
    except Exception as exc:  # noqa: BLE001 — preflight 要抓所有失敗型態
        detail = f"{type(exc).__name__}: {exc}"
        record(name, FAIL if fatal else WARN, detail)
        if "--verbose" in sys.argv:
            traceback.print_exc()


def check_nvidia_smi() -> str:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return out.stdout.strip()


def check_torch_build() -> str:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() 為 False——這台機器的 torch 看不到 GPU")
    cap = torch.cuda.get_device_capability()
    arch_list = torch.cuda.get_arch_list()
    sm_tag = f"sm_{cap[0]}{cap[1]}"
    detail = (
        f"torch={torch.__version__} cuda={torch.version.cuda} "
        f"device={torch.cuda.get_device_name(0)} capability={sm_tag} "
        f"compiled_archs={arch_list}"
    )
    if sm_tag not in arch_list:
        raise RuntimeError(
            f"這個 torch build 沒有編 {sm_tag} kernel（只有 {arch_list}）——"
            f"Blackwell 需 cu128 以上的 wheel。{detail}"
        )
    return detail


def check_gpu_matmul() -> str:
    """真的在 GPU 上算一次——arch_list 對了但 kernel 壞掉時只有實算抓得到。"""
    import torch

    a = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
    c = (a @ b).float()
    torch.cuda.synchronize()
    if not torch.isfinite(c).all():
        raise RuntimeError("bf16 matmul 結果含 NaN/Inf")
    return f"bf16 2048x2048 matmul OK, mean={c.mean().item():.4f}"


def check_bnb_4bit() -> str:
    """QLoRA 的地基：4-bit 量化線性層必須能在這張卡上前向傳播。"""
    import bitsandbytes as bnb
    import torch

    layer = bnb.nn.Linear4bit(512, 512, bias=False, compute_dtype=torch.bfloat16).cuda()
    x = torch.randn(4, 512, device="cuda", dtype=torch.bfloat16)
    y = layer(x)
    torch.cuda.synchronize()
    if not torch.isfinite(y).all():
        raise RuntimeError("Linear4bit 前向結果含 NaN/Inf")
    return f"bitsandbytes={bnb.__version__} Linear4bit forward OK shape={tuple(y.shape)}"


def check_unsloth_import() -> str:
    import unsloth
    from unsloth import FastVisionModel  # noqa: F401 — 只驗 import 路徑存在

    return f"unsloth={getattr(unsloth, '__version__', 'unknown')} FastVisionModel 可載入"


def check_vram(min_gb: float = 60.0) -> str:
    import torch

    total_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    if total_gb < min_gb:
        raise RuntimeError(
            f"VRAM 僅 {total_gb:.1f} GB——需要 >={min_gb:.0f}GB"
        )
    return f"VRAM {total_gb:.1f} GB (>= {min_gb:.0f} GB)"


def main() -> int:
    parser = argparse.ArgumentParser(description="GPU 資格前置檢查")
    parser.add_argument("--quick", action="store_true", help="只驗 torch 核心，跳過 bnb/unsloth")
    parser.add_argument("--verbose", action="store_true", help="失敗時印完整 traceback")
    parser.add_argument(
        "--min-vram-gb", type=float, default=0.0,
        help="VRAM 最低門檻 GB（預設 0＝不擋；31B 4-bit 訓練建議 30，BF16 推論建議 70）",
    )
    args = parser.parse_args()

    print("===== GPU preflight =====", flush=True)
    check("nvidia-smi", check_nvidia_smi)
    check("torch build vs GPU arch", check_torch_build)
    check("VRAM 容量", lambda: check_vram(min_gb=args.min_vram_gb))
    check("GPU bf16 matmul 實算", check_gpu_matmul)
    if not args.quick:
        check("bitsandbytes 4-bit（QLoRA 地基）", check_bnb_4bit)
        check("unsloth import", check_unsloth_import)

    failed = [r for r in results if r[1] == FAIL]
    print("\n===== 結論 =====", flush=True)
    if failed:
        print(f"{len(failed)} 項 FAIL——停手，別下載模型：", flush=True)
        for name, _, detail in failed:
            print(f"  - {name}: {detail}", flush=True)
        print("\n多半是 torch wheel 沒編這張卡的 arch。修法："
              "pip install --upgrade --index-url https://download.pytorch.org/whl/cu128 torch torchvision",
              flush=True)
        return 1
    print("全部通過——可以繼續下載模型", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
