"""Build the public overview from original page captures and local narration."""

import argparse
import hashlib
import json
import os
import subprocess
import textwrap
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")
import numpy as np
import onnxruntime as ort
import soundfile as sf
from kokoro_onnx import Kokoro
from PIL import Image, ImageDraw, ImageFont

BG = "#0b1423"
FG = "#f4f6f9"
MUTED = "#aebdd0"
TEAL = "#52dfbd"
FONT = "/usr/share/fonts/TTF/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"


def run(args):
    subprocess.run(args, check=True)


def stamp(t, ass=False):
    n = round(t * (100 if ass else 1000))
    h, n = divmod(n, 360000 if ass else 3600000)
    m, n = divmod(n, 6000 if ass else 60000)
    s, n = divmod(n, 100 if ass else 1000)
    return f"{h}:{m:02}:{s:02}.{n:02}" if ass else f"{h:02}:{m:02}:{s:02},{n:03}"


def text(d, xy, value, size=28, color=FG, bold=False, spacing=14):
    d.multiline_text(
        xy,
        value,
        font=ImageFont.truetype(BOLD if bold else FONT, size),
        fill=color,
        spacing=spacing,
    )


def panel(d, box):
    d.rounded_rectangle(box, radius=20, fill="#121f32", outline="#26364c", width=2)


def background(scene):
    im = Image.new("RGB", (1920, 1080), BG)
    d = ImageDraw.Draw(im)
    text(d, (96, 42), "AGENTS SHOULD SURVIVE FAILURE", 23, TEAL, True)
    text(d, (1390, 46), "DURABLE WORKFLOWS / HUMAN AUTHORITY", 16, MUTED)
    d.line((96, 98, 1824, 98), fill="#27364a", width=2)
    kind = scene.get("kind", "standard")
    if kind == "close":
        text(d, (96, 160), "TRY THE WORKFLOW LOCALLY", 21, TEAL, True)
        text(d, (96, 220), "Agents Should\nSurvive Failure", 76, FG, True, 8)
        text(d, (98, 455), "Two attempts.\nOne committed outcome.", 49, FG, True, 16)
        text(d, (98, 655), "Temporal   /   PostgreSQL   /   FastAPI   /   Python", 25, MUTED)
        d.line((96, 720, 830, 720), fill=TEAL, width=4)
        text(d, (98, 767), "EXPLORE THE CODE + REPRODUCE THE FAILURE", 20, TEAL, True)
        text(d, (98, 813), "github.com/kabbersokhi-boop/agents-should-survive-failure", 27, FG)
        return im
    text(d, (96, 144), scene["eyebrow"], 19, TEAL, True)
    if kind == "wide":
        text(d, (96, 195), scene["title"], 63, FG, True, 12)
    else:
        text(d, (96, 240), scene["title"], 48, FG, True, 16)
        for j, line in enumerate(scene.get("support", [])):
            text(d, (98, 460 + j * 54), line, 24, MUTED)
        d.line((98, 650, 242, 650), fill=TEAL, width=4)
    if scene["id"] == "hook":
        text(d, (98, 758), "SIMULATED SUPPLIER", 16, MUTED)
    if kind == "architecture":
        nodes = [
            (680, 240, 1120, 405, "Human approval", "Authority to decide"),
            (1240, 240, 1800, 405, "Temporal", "History + durable coordination"),
            (1240, 635, 1800, 800, "PostgreSQL", "Identity + committed business effects"),
        ]
        for x, y, x2, y2, title, sub in nodes:
            panel(d, (x, y, x2, y2))
            text(d, (x + 28, y + 30), title, 31, FG, True)
            text(d, (x + 28, y + 100), sub, 20, MUTED)
        d.line((1120, 322, 1240, 322), fill=TEAL, width=4)
        d.polygon([(1228, 314), (1240, 322), (1228, 330)], fill=TEAL)
        d.line((1520, 405, 1520, 635), fill=TEAL, width=4)
        d.polygon([(1512, 623), (1520, 635), (1528, 623)], fill=TEAL)
        text(d, (1570, 500), "Retry the activity", 20, MUTED)
        panel(d, (680, 635, 1120, 800))
        text(d, (708, 668), "Model advice", 30, FG, True)
        text(d, (708, 733), "Bounded evidence / no approval authority", 17, MUTED)
        d.line((900, 635, 900, 405), fill="#597088", width=2)
        d.polygon([(892, 417), (900, 405), (908, 417)], fill="#597088")
        return im
    panel(d, (96, 422, 1824, 914) if kind == "wide" else (660, 190, 1824, 900))
    return im


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--tools", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--only", nargs="+")
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    story = json.loads(Path(__file__).with_name("story.json").read_text())
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(a.tools / "kokoro-v1.0.onnx"), options, providers=["CPUExecutionProvider"]
    )
    voice = Kokoro.from_session(session, str(a.tools / "voices-v1.0.bin"))
    timeline = []
    captions = []
    joined = []
    at = 0
    rate = 24000
    for i, s in enumerate(story["scenes"]):
        audio = []
        cursor = 0.3
        for line in s["lines"]:
            key = hashlib.sha256((story["voice"] + line).encode()).hexdigest()[:12]
            wav = a.work / (key + ".wav")
            if wav.exists():
                samples, sr = sf.read(wav, dtype="float32")
            else:
                samples, sr = voice.create(
                    line, voice=story["voice"], speed=story["speed"], lang="en-us"
                )
                sf.write(wav, samples, sr, subtype="PCM_16")
            assert sr == rate
            captions.append(
                {
                    "start": at + cursor,
                    "end": at + cursor + len(samples) / rate,
                    "text": line,
                }
            )
            audio.extend([samples, np.zeros(round(0.18 * rate), dtype=np.float32)])
            cursor += (len(samples) / rate) + 0.18
        duration = np.ceil(max(s["duration"], cursor + 0.4) * 30) / 30
        unit = np.concatenate([np.zeros(round(0.3 * rate), dtype=np.float32), *audio])
        unit = np.pad(unit, (0, round(duration * rate) - len(unit)))
        joined.append(unit)
        s = {**s, "at": at, "duration": duration}
        timeline.append(s)
        at += duration
        background(s).save(a.work / f"background-{i:02}.png")
        print("Prepared", s["id"], round(duration, 2), flush=True)
    sf.write(a.work / "voice.wav", np.concatenate(joined), rate, subtype="PCM_16")
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(a.work / "voice.wav"),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=7",
            "-ar",
            "48000",
            str(a.work / "voice-master.wav"),
        ]
    )
    (a.work / "timeline.json").write_text(
        json.dumps(
            {"run_id": story["run_id"], "duration": at, "scenes": timeline, "captions": captions},
            indent=2,
        )
        + "\n"
    )
    srt = []
    for i, c in enumerate(captions, 1):
        srt.append(
            f"{i}\n{stamp(c['start'])} --> {stamp(c['end'] + 0.08)}\n"
            + "\n".join(textwrap.wrap(c["text"], width=84))
            + "\n"
        )
    (a.work / "overview.en.srt").write_text("\n".join(srt))
    if a.prepare_only:
        return
    parts = []
    sources = (
        json.loads((a.work / "source-hashes.json").read_text())
        if (a.work / "source-hashes.json").exists()
        else {}
    )
    for i, s in enumerate(timeline):
        part = a.work / f"scene-{i:02}.mp4"
        if a.only and s["id"] not in a.only and part.exists():
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(part),
                ],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0 and abs(float(probe.stdout.strip()) - s["duration"]) < 0.05:
                parts.append(part)
                continue
        d = s["duration"]
        kind = s.get("kind", "standard")
        args = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-filter_complex_threads",
            "2",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(a.work / f"background-{i:02}.png"),
        ]
        filters = []
        prev = "0:v"
        for j, shot in enumerate(s.get("shots", [])):
            source = a.raw / shot["file"]
            sources[shot["file"]] = hashlib.sha256(source.read_bytes()).hexdigest()
            args += ["-ss", str(shot["start"]), "-i", str(source)]
            box = (112, 438, 1696, 460) if kind == "wide" else (680, 210, 1124, 670)
            x, y, width, height = box
            cx, cy, cw, ch = shot["crop"]
            label = f"clip{j}"
            filters.append(
                f"[{j + 1}:v]trim=duration={min(shot['available'], d)},setpts=PTS-STARTPTS,"
                f"crop={cw}:{ch}:{cx}:{cy},fps=30,scale={width}:{height}:"
                "force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x121f32,"
                f"tpad=stop_mode=clone:stop_duration={d},trim=duration={d}[{label}]"
            )
            out = f"comp{j}"
            filters.append(f"[{prev}][{label}]overlay={x}:{y}:shortest=1[{out}]")
            prev = out
        # A single caption layer. The original recordings contain no editorial footers.
        header = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
            "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
            "Encoding\nStyle: Caption,DejaVu Sans,31,&H00FFFFFF,&H00FFFFFF,&H0023140B,&H0023140B,"
            "0,0,0,0,100,100,0,0,1,1,0,2,140,140,36,1\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        events = []
        for c in captions:
            if s["at"] <= c["start"] < s["at"] + d:
                txt = r"\N".join(textwrap.wrap(c["text"], width=84))
                events.append(
                    f"Dialogue: 1,{stamp(c['start'] - s['at'], True)},"
                    f"{stamp(c['end'] - s['at'] + 0.08, True)},"
                    f"Caption,,0,0,0,,{{\\fad(90,90)}}{txt}"
                )
        # Editorial outlines point at actual recorded fields without replacing their values.
        focus = {"hook": (838, 592, 1644, 662), "history": (1140, 620, 1250, 666)}.get(s["id"])
        if focus:
            x1, y1, x2, y2 = focus
            focus_end = d - 0.4
            events.append(
                f"Dialogue: 0,0:00:00.70,{stamp(focus_end, True)},Caption,,0,0,0,,"
                "{\\an7\\pos(0,0)\\p1\\1a&HFF&\\3c&HBDDF52&\\bord3\\shad0\\fad(250,250)}"
                f"m {x1} {y1} l {x2} {y1} {x2} {y2} {x1} {y2} {x1} {y1}"
            )
        # An accent line reveals the current shot, then the composition fades.
        ms = round(d * 1000)
        events.append(
            f"Dialogue: 0,0:00:00.00,{stamp(d, True)},Caption,,0,0,0,,"
            "{\\an7\\pos(0,0)\\p1\\bord0\\1c&HBDDF52&\\clip(96,932,96,936)"
            f"\\t(0,{ms},\\clip(96,932,1824,936))}}"
            "m 96 932 l 1824 932 1824 936 96 936"
        )
        ass = a.work / f"scene-{i:02}.ass"
        ass.write_text(header + "\n".join(events) + "\n")
        filters.append(
            f"[{prev}]ass={ass},fade=t=in:st=0:d=0.18,fade=t=out:st={d - 0.18}:d=0.18[out]"
        )
        part = a.work / f"scene-{i:02}.mp4"
        args += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-an",
            "-t",
            str(d),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "4",
            str(part),
        ]
        run(args)
        parts.append(part)
        print("Rendered", s["id"], flush=True)
    for s in timeline:
        for shot in s.get("shots", []):
            if shot["file"] not in sources:
                sources[shot["file"]] = hashlib.sha256(
                    (a.raw / shot["file"]).read_bytes()
                ).hexdigest()
    (a.work / "source-hashes.json").write_text(json.dumps(sources, indent=2) + "\n")
    concat = a.work / "concat.txt"
    concat.write_text("".join(f"file '{p}'\n" for p in parts))
    metadata = a.work / "chapters.ffmetadata"
    metadata.write_text(
        ";FFMETADATA1\ntitle=Agents Should Survive Failure | Approve Once, Recover Safely\n"
        + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(s['at'] * 1000)}\n"
            f"END={round((s['at'] + s['duration']) * 1000)}\n"
            f"title={s['title'].replace(chr(10), ' ')}\n"
            for s in timeline
        )
    )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-i",
            str(a.work / "voice-master.wav"),
            "-f",
            "ffmetadata",
            "-i",
            str(metadata),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map_metadata",
            "2",
            "-map_chapters",
            "2",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata",
            "comment=Synthetic supplier, deterministic advisory provider, real Temporal and "
            "PostgreSQL. Original recorded application evidence; local notification record, "
            "not delivered email.",
            "-movflags",
            "+faststart",
            "-t",
            str(at),
            str(a.output),
        ]
    )
    print("Finished", round(at, 2), flush=True)


if __name__ == "__main__":
    main()
