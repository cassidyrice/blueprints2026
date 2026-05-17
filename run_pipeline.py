#!/usr/bin/env python3
"""
run_pipeline.py — Cardology video pipeline orchestrator

Steps:
  1. calculate_blueprint   → blueprint.json
  2. generate_reading      → reading.txt  (Claude narration)
  3. OpenAI TTS            → voiceover.mp3
  4. build_composition_v2  → composition/index.html  (HyperFrames)
  5. hyperframes render    → <slug>.mp4

Usage:
  python3 run_pipeline.py <month> <day> <year> "<question>"
  python3 run_pipeline.py 6 30 1984 "What should I focus on this year?"
  python3 run_pipeline.py 6 30 1984 "What should I focus?" --out /tmp/myreading
  python3 run_pipeline.py 6 30 1984 "What should I focus?" --no-render
"""

import argparse
import json
import os
import sys
import subprocess
from datetime import date, datetime
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
DOWNLOADS  = Path.home() / "Downloads"
HYPERFRAMES_DIR = Path.home() / "hyperframes"
OUTPUT_ROOT = Path.home() / "output"

# Locate build_composition_v2.py — prefer local copy, fall back to Downloads
_comp_candidates = [
    SCRIPT_DIR / "build_composition_v2.py",
    DOWNLOADS / "build_composition_v2.py",
]
COMP_SCRIPT = next((p for p in _comp_candidates if p.exists()), None)

sys.path.insert(0, str(SCRIPT_DIR))
if COMP_SCRIPT:
    sys.path.insert(0, str(COMP_SCRIPT.parent))

# ── Imports ───────────────────────────────────────────────────────────────────

from calculate_blueprint import calculate_blueprint
from generate_reading import generate_reading, build_reading_prompt

if COMP_SCRIPT:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("build_composition_v2", COMP_SCRIPT)
    _mod  = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    build_composition = _mod.build_composition
else:
    build_composition = None


# ── TTS via OpenAI ────────────────────────────────────────────────────────────

def tts_openai(text: str, out_path: Path, voice: str = "onyx") -> Path:
    """Generate speech via OpenAI TTS API → mp3."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"  [TTS] Generating voiceover ({len(text)} chars, voice={voice})...")
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    response.stream_to_file(str(out_path))
    size_kb = out_path.stat().st_size // 1024
    print(f"  [TTS] Saved: {out_path} ({size_kb} KB)")
    return out_path


# ── Audio duration probe ──────────────────────────────────────────────────────

def probe_duration(audio_path: Path) -> int:
    """Return audio duration in seconds via ffprobe, defaulting to 150."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return int(float(dur)) + 2  # +2s buffer
    except Exception:
        pass
    return 150


# ── HyperFrames render ────────────────────────────────────────────────────────

def render_hyperframes(composition_dir: Path, output_mp4: Path) -> bool:
    """
    Render the HyperFrames composition to mp4.
    Tries:
      1. global `hyperframes` binary
      2. bun run from source (~/hyperframes)
    Returns True on success.
    """
    # Candidate commands
    commands = []

    # 1. Global binary
    import shutil
    if shutil.which("hyperframes"):
        commands.append(["hyperframes", "render", str(composition_dir),
                         "--output", str(output_mp4)])

    # 2. bun run from source
    if HYPERFRAMES_DIR.exists() and shutil.which("bun"):
        commands.append(
            ["bun", "run", "--cwd", str(HYPERFRAMES_DIR),
             "packages/cli/src/cli.ts", "render", str(composition_dir),
             "--output", str(output_mp4)]
        )

    # 3. npx tsx from source
    if HYPERFRAMES_DIR.exists():
        commands.append(
            ["npx", "tsx",
             str(HYPERFRAMES_DIR / "packages" / "cli" / "src" / "cli.ts"),
             "render", str(composition_dir),
             "--output", str(output_mp4)]
        )

    for cmd in commands:
        print(f"  [Render] {' '.join(cmd[:3])}...")
        try:
            subprocess.run(cmd, check=True)
            print(f"  [Render] Done: {output_mp4}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  [Render] Failed ({e}), trying next...")

    print("\n  [Render] Could not find a working HyperFrames render command.")
    print("  To render manually, run one of:")
    print(f"    cd {HYPERFRAMES_DIR} && bun run build && hyperframes render {composition_dir} --output {output_mp4}")
    print(f"    hyperframes render {composition_dir} --output {output_mp4}")
    return False


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    month: int,
    day: int,
    year: int,
    question: str,
    out_dir: Path | None = None,
    voice: str = "onyx",
    no_render: bool = False,
) -> dict:
    slug = f"cardology-{month:02d}-{day:02d}-{year}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out = out_dir or (OUTPUT_ROOT / slug)
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*60}")
    print(f"  Cardology Pipeline: {month}/{day}/{year}")
    print(f"  Question: {question}")
    print(f"  Output:   {out}")
    print(f"{'='*60}\n")

    results = {"output_dir": str(out), "slug": slug}

    # ── Step 1: Blueprint ─────────────────────────────────────────────────────
    print("[1/5] Calculating blueprint...")
    today = date.today()
    bp = calculate_blueprint(month, day, year, today)
    bp_path = out / "blueprint.json"
    bp_path.write_text(json.dumps(bp, indent=2, default=str), encoding="utf-8")
    a = bp.get("archetype", {})
    print(f"  Birth Card: {a.get('birth_card','?')} | PRC: {a.get('prc','?')}")
    results["blueprint"] = str(bp_path)

    # ── Step 2: Generate reading (narration text) ──────────────────────────────
    print("\n[2/5] Generating reading (Claude)...")
    reading_text = generate_reading(month, day, year, question)
    reading_path = out / "reading.txt"
    reading_path.write_text(reading_text, encoding="utf-8")
    print(f"  Reading saved: {reading_path} ({len(reading_text)} chars)")
    results["reading"] = str(reading_path)

    # ── Step 3: TTS → voiceover.mp3 ───────────────────────────────────────────
    print("\n[3/5] Generating voiceover (OpenAI TTS)...")
    if not os.environ.get("OPENAI_API_KEY"):
        print("  WARNING: OPENAI_API_KEY not set — skipping TTS.")
        print("  Set OPENAI_API_KEY and re-run, or provide voiceover.mp3 manually.")
        vo_path = out / "voiceover.mp3"
        results["voiceover"] = None
        duration_sec = 150
    else:
        vo_path = out / "voiceover.mp3"
        tts_openai(reading_text, vo_path, voice=voice)
        duration_sec = probe_duration(vo_path)
        print(f"  Duration: {duration_sec}s")
        results["voiceover"] = str(vo_path)

    # ── Step 4: Build HyperFrames composition ─────────────────────────────────
    print("\n[4/5] Building visual composition...")
    if not build_composition:
        print(f"  ERROR: build_composition_v2.py not found.")
        print(f"  Expected at: {SCRIPT_DIR}/build_composition_v2.py  or  {DOWNLOADS}/build_composition_v2.py")
        results["composition"] = None
    else:
        comp_dir = out / "composition"
        html_path = build_composition(bp, str(vo_path), comp_dir, duration_sec)
        print(f"  Composition: {html_path}")
        results["composition"] = str(html_path)

    # ── Step 5: Render ────────────────────────────────────────────────────────
    if no_render:
        print("\n[5/5] Render skipped (--no-render).")
        results["video"] = None
    elif results.get("composition"):
        print("\n[5/5] Rendering video...")
        mp4_path = out / f"{slug}.mp4"
        success = render_hyperframes(out / "composition", mp4_path)
        results["video"] = str(mp4_path) if success else None
    else:
        print("\n[5/5] Render skipped (no composition).")
        results["video"] = None

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  Pipeline complete.")
    for k, v in results.items():
        if k != "slug":
            print(f"  {k:15s} {v or '(skipped)'}")
    print(f"{'='*60}\n")

    return results


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cardology video pipeline: blueprint → reading → TTS → composition → render"
    )
    parser.add_argument("month",    type=int, help="Birth month (1-12)")
    parser.add_argument("day",      type=int, help="Birth day (1-31)")
    parser.add_argument("year",     type=int, help="Birth year (e.g. 1984)")
    parser.add_argument("question", type=str, help="Client question in quotes")
    parser.add_argument("--out",    type=Path, default=None,
                        help="Output directory (default: ~/output/cardology-MM-DD-YYYY-...)")
    parser.add_argument("--voice",  default="onyx",
                        choices=["alloy","echo","fable","onyx","nova","shimmer"],
                        help="OpenAI TTS voice (default: onyx)")
    parser.add_argument("--no-render", action="store_true",
                        help="Skip HyperFrames render step (stops after composition HTML)")
    args = parser.parse_args()

    run_pipeline(
        month=args.month,
        day=args.day,
        year=args.year,
        question=args.question,
        out_dir=args.out,
        voice=args.voice,
        no_render=args.no_render,
    )
