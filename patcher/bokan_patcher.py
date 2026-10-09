"""Windows GUI / CLI patcher for the Bokan unofficial Korean translation V1.0.

Uses the public BPS specification (byuu, public domain). Python standard library only.
The packaged executable embeds the exact patch and expected disc hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import zlib


class PatchError(ValueError):
    pass


def resources() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1] / "patch"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def apply_bps(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 16 or patch[:4] != b"BPS1":
        raise PatchError("올바른 BPS 패치가 아닙니다.")
    source_crc, target_crc, patch_crc = struct.unpack_from("<III", patch, len(patch) - 12)
    if zlib.crc32(patch[:-4]) != patch_crc:
        raise PatchError("패치 파일이 손상되었습니다.")
    if zlib.crc32(source) != source_crc:
        raise PatchError("원본 데이터의 CRC32가 일치하지 않습니다.")
    pos = 4
    limit = len(patch) - 12

    def number() -> int:
        nonlocal pos
        value, shift = 0, 1
        while True:
            if pos >= limit or shift > (1 << 63):
                raise PatchError("패치 명령이 잘못되었습니다.")
            x = patch[pos]
            pos += 1
            value += (x & 127) * shift
            if x & 128:
                return value
            shift <<= 7
            value += shift

    source_size, target_size, metadata_size = number(), number(), number()
    if source_size != len(source) or target_size != len(source):
        raise PatchError("지원하는 원본/결과 크기와 일치하지 않습니다.")
    if metadata_size > limit - pos:
        raise PatchError("패치 메타데이터가 손상되었습니다.")
    pos += metadata_size
    out = bytearray()
    source_cursor = target_cursor = 0
    while len(out) < target_size:
        command = number()
        action, length = command & 3, (command >> 2) + 1
        if length > target_size - len(out):
            raise PatchError("패치 결과 크기를 초과하는 명령입니다.")
        if action == 0:
            out.extend(source[len(out):len(out) + length])
        elif action == 1:
            if pos + length > limit:
                raise PatchError("패치 데이터가 부족합니다.")
            out.extend(patch[pos:pos + length])
            pos += length
        else:
            offset = number()
            delta = -(offset >> 1) if offset & 1 else offset >> 1
            if action == 2:
                source_cursor += delta
                if source_cursor < 0 or source_cursor + length > len(source):
                    raise PatchError("원본 복사 범위가 잘못되었습니다.")
                out.extend(source[source_cursor:source_cursor + length])
                source_cursor += length
            else:
                target_cursor += delta
                if target_cursor < 0 or target_cursor >= len(out):
                    raise PatchError("결과 복사 범위가 잘못되었습니다.")
                for _ in range(length):
                    out.append(out[target_cursor])
                    target_cursor += 1
    if pos != limit or zlib.crc32(out) != target_crc:
        raise PatchError("패치 결과 검증에 실패했습니다.")
    return bytes(out)


def read_cue(path: Path) -> tuple[list[str], list[dict]]:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932", "cp949"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    else:
        raise PatchError("CUE 파일의 문자 인코딩을 읽을 수 없습니다.")
    lines = text.splitlines()
    entries = []
    for i, line in enumerate(lines):
        m = re.fullmatch(r'\s*FILE\s+"([^"]+)"\s+BINARY\s*', line, re.I)
        if m:
            filename = Path(m[1].replace("\\", os.sep))
            resolved = filename if filename.is_absolute() else path.parent / filename
            entries.append({"line": i, "path": resolved.resolve(), "tracks": []})
        elif re.match(r"\s*FILE\b", line, re.I):
            raise PatchError("BINARY 형식의 분할 BIN/CUE만 지원합니다.")
        elif (m := re.fullmatch(r"\s*TRACK\s+(\d+)\s+(\S+)\s*", line, re.I)):
            if not entries:
                raise PatchError("CUE 파일의 TRACK/FILE 순서가 잘못되었습니다.")
            entries[-1]["tracks"].append((int(m[1]), m[2].upper()))
    expected = [[(1, "MODE1/2352")]] + [[(i, "AUDIO")] for i in range(2, 41)]
    if [entry["tracks"] for entry in entries] != expected:
        raise PatchError("40개 트랙으로 분할된 일본판 BIN/CUE가 필요합니다. CHD나 단일 BIN은 지원하지 않습니다.")
    if len({entry["path"] for entry in entries}) != 40:
        raise PatchError("트랙 파일 경로가 중복되어 있습니다.")
    return lines, entries


def patch_disc(cue: Path, output: Path | None, progress=lambda message: None,
               verify_only: bool = False) -> dict:
    manifest = json.loads((resources() / "manifest.json").read_text("utf-8"))
    patch_path = resources() / "Bokan-KR-V1.0.bps"
    if sha256_file(patch_path) != manifest["patch_sha256"]:
        raise PatchError("패치 파일의 SHA-256이 일치하지 않습니다. 배포 파일을 다시 받아 주세요.")
    cue = cue.expanduser().resolve()
    lines, entries = read_cue(cue)
    for index, (entry, expected) in enumerate(zip(entries, manifest["tracks"]), 1):
        path = entry["path"]
        progress(f"원본 검증 중: 트랙 {index:02d}/40")
        if not path.is_file():
            raise PatchError(f"트랙 {index:02d} 파일이 없습니다: {path.name}")
        if path.stat().st_size != expected["bytes"] or sha256_file(path) != expected["sha256"]:
            extra = "" if index == 1 else " 오디오가 누락되거나 다른 덤프일 수 있습니다."
            raise PatchError(f"트랙 {index:02d}의 SHA-256이 지원 원본과 다릅니다.{extra}")
    progress("한글 패치와 결과 해시 검증 중")
    source = entries[0]["path"].read_bytes()
    target = apply_bps(source, patch_path.read_bytes())
    del source
    target_sha = hashlib.sha256(target).hexdigest()
    if target_sha != manifest["target_sha256"]:
        raise PatchError("한글판 Track 01의 SHA-256이 일치하지 않습니다.")
    if verify_only:
        return {"passed": True, "verified_tracks": 40, "target_sha256": target_sha, "verify_only": True}
    if output is None:
        raise PatchError("출력 폴더를 지정해 주세요.")
    output = output.expanduser().resolve()
    if output.exists():
        raise PatchError("출력 폴더가 이미 있습니다. 덮어쓰지 않으므로 새 폴더 이름을 지정해 주세요.")
    if not output.parent.is_dir():
        raise PatchError("출력 폴더의 상위 폴더가 없습니다.")
    temp = Path(tempfile.mkdtemp(prefix=".bokan-patch-", dir=output.parent))
    try:
        target_names = ["Bokan-KR-Track01.bin"] + [f"Bokan-KR-Track{i:02d}.bin" for i in range(2, 41)]
        (temp / target_names[0]).write_bytes(target)
        del target
        for index, entry in enumerate(entries[1:], 2):
            progress(f"오디오 복사 및 검증 중: 트랙 {index:02d}/40")
            dest = temp / target_names[index - 1]
            shutil.copyfile(entry["path"], dest)
            if sha256_file(dest) != manifest["tracks"][index - 1]["sha256"]:
                raise PatchError(f"트랙 {index:02d} 복사 후 검증에 실패했습니다.")
        if sha256_file(temp / target_names[0]) != target_sha:
            raise PatchError("저장된 한글판 Track 01 검증에 실패했습니다.")
        for entry, filename in zip(entries, target_names):
            lines[entry["line"]] = f'FILE "{filename}" BINARY'
        (temp / "Bokan-KR.cue").write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = {"passed": True, "version": "1.0", "verified_tracks": 40,
                  "output_cue": str(output / "Bokan-KR.cue"), "target_sha256": target_sha,
                  "source_tracks_modified": False, "audio_tracks_copied_unchanged": 39}
        (temp / "patch-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
        # Windows rename refuses an existing destination; the GUI never overwrites a disc.
        if output.exists():
            raise PatchError("출력 폴더가 작업 중 생성되었습니다. 다른 폴더를 선택해 주세요.")
        temp.rename(output)
        return report
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title("보칸 한글화 V1.0 패처")
    root.geometry("720x410")
    root.minsize(660, 400)
    root.option_add("*Font", "{Malgun Gothic} 10")
    root.configure(background="#f4f5f7")
    panel = ttk.Frame(root, padding=24)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="타임 보칸 · 비공식 한글화 V1.0", font=("Malgun Gothic", 17, "bold")).pack(anchor="w")
    ttk.Label(panel, text="40트랙으로 분할된 일본판 BIN/CUE를 선택하세요.\n원본을 보존하고, 음악 트랙을 포함한 한글판을 새 폴더에 만듭니다.").pack(anchor="w", pady=(8, 18))
    cue_value, output_value = tk.StringVar(), tk.StringVar()
    controls = []

    def choose_cue():
        value = filedialog.askopenfilename(title="원본 일본판 CUE 선택", filetypes=[("CUE 파일", "*.cue"), ("모든 파일", "*.*")])
        if value:
            cue_value.set(value)
            if not output_value.get():
                output_value.set(str(Path(value).parent / "Bokan-KR"))

    def choose_output():
        value = filedialog.askdirectory(title="한글판을 저장할 상위 폴더 선택")
        if value:
            output_value.set(str(Path(value) / "Bokan-KR"))

    for label, variable, command in [("원본 CUE", cue_value, choose_cue), ("새 출력 폴더", output_value, choose_output)]:
        ttk.Label(panel, text=label).pack(anchor="w")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(3, 10))
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        button = ttk.Button(row, text="찾아보기", command=command)
        button.pack(side="right", padx=(8, 0))
        controls.extend((entry, button))
    status = tk.StringVar(value="원본 트랙 및 한글판 결과를 SHA-256으로 검증합니다.")
    ttk.Label(panel, textvariable=status, wraplength=660).pack(anchor="w", pady=(8, 4))
    bar = ttk.Progressbar(panel, mode="indeterminate")
    bar.pack(fill="x", pady=(0, 12))
    events = queue.Queue()
    busy = False

    def worker(cue, output):
        try:
            result = patch_disc(Path(cue), Path(output), lambda m: events.put(("progress", m)))
            events.put(("done", result))
        except Exception as e:
            events.put(("error", str(e)))

    def start():
        nonlocal busy
        if not cue_value.get().strip() or not output_value.get().strip():
            messagebox.showerror("파일 선택", "원본 CUE와 새 출력 폴더를 지정해 주세요.", parent=root)
            return
        busy = True
        for widget in controls:
            widget.configure(state="disabled")
        bar.start(12)
        threading.Thread(target=worker, args=(cue_value.get().strip(), output_value.get().strip()), daemon=True).start()

    action = ttk.Button(panel, text="한글판 만들기", command=start)
    action.pack(anchor="e")
    controls.append(action)

    def poll():
        nonlocal busy
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "progress":
                    status.set(value)
                    continue
                busy = False
                bar.stop()
                for widget in controls:
                    widget.configure(state="normal")
                if kind == "done":
                    status.set("완료: Bokan-KR.cue를 에뮬레이터에서 실행하세요.")
                    messagebox.showinfo("한글판 생성 완료", f"{value['output_cue']}\n\n40개 트랙과 결과 해시를 확인했습니다.\n클리어 세이브는 설명서에 따라 별도로 적용하세요.", parent=root)
                else:
                    status.set("작업을 완료하지 못했습니다.")
                    messagebox.showerror("패치 오류", value, parent=root)
        except queue.Empty:
            pass
        root.after(100, poll)

    def close():
        if busy:
            messagebox.showinfo("작업 진행 중", "파일을 검증·복사하고 있습니다. 작업이 끝난 뒤 닫아 주세요.", parent=root)
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bokan Korean V1.0 patcher")
    parser.add_argument("--cue", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Write UTF-8 JSON result (also for windowed EXE)")
    args = parser.parse_args()
    if not args.cue:
        gui()
        return 0
    try:
        result = patch_disc(args.cue, args.output, verify_only=args.verify_only)
        code = 0
    except Exception as e:
        result, code = {"passed": False, "error": str(e)}, 1
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, "utf-8")
    if sys.stdout:
        print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
