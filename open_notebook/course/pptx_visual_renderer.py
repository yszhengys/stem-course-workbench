"""Restricted local PPTX-to-PNG rendering for Course evidence previews."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


class PptxVisualUnavailable(RuntimeError):
    """Raised when the optional local rendering runtime cannot complete."""


class PptxVisualRejected(ValueError):
    """Raised when an input or rendered asset fails a security invariant."""


PopenFactory = Callable[..., Any]
ConverterLocator = Callable[[], str | None]


class PptxVisualRenderer:
    """Validate a PPTX, convert it in isolation, and rasterize bounded PNGs."""

    MAX_SOURCE_BYTES = 100 * 1024 * 1024
    MAX_MEMBERS = 10_000
    MAX_EXPANDED_BYTES = 500 * 1024 * 1024
    MAX_MEMBER_BYTES = 100 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 1_000
    MAX_PDF_BYTES = 250 * 1024 * 1024
    DEFAULT_MAX_IMAGE_BYTES = 12 * 1024 * 1024
    DEFAULT_MAX_TOTAL_BYTES = 120 * 1024 * 1024
    OUTPUT_WIDTH = 1280
    MAX_OUTPUT_HEIGHT = 10_000
    _SLIDE_MEMBER = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")
    _SHA256 = re.compile(r"^[0-9a-f]{64}$")
    _PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
    _DANGEROUS_CONTENT_TYPES = (
        b"macroenabled",
        b"vbaproject",
        b"activex",
        b"oleobject",
    )

    def __init__(
        self,
        *,
        converter: Path | None = None,
        converter_locator: ConverterLocator | None = None,
        popen_factory: PopenFactory = subprocess.Popen,
        timeout_seconds: float = 120,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("PPTX renderer timeout must be positive")
        if max_image_bytes <= 0 or max_total_bytes < max_image_bytes:
            raise ValueError("PPTX renderer byte limits are invalid")
        self.converter = converter
        self.converter_locator = converter_locator
        self.popen_factory = popen_factory
        self.timeout_seconds = timeout_seconds
        self.max_image_bytes = max_image_bytes
        self.max_total_bytes = max_total_bytes

    @staticmethod
    def _default_converter_locator() -> str | None:
        located = shutil.which("soffice")
        if located:
            return located
        macos_binary = Path(
            "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        )
        if macos_binary.is_file() and os.access(macos_binary, os.X_OK):
            return str(macos_binary)
        return None

    def locate_converter(self) -> Path | None:
        if self.converter is not None:
            return self.converter
        locator = self.converter_locator or self._default_converter_locator
        value = locator()
        return Path(value) if value else None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _safe_member_name(cls, name: str) -> PurePosixPath:
        if not name or "\x00" in name or "\\" in name or name.startswith("/"):
            raise PptxVisualRejected("PPTX contains an unsafe ZIP member path.")
        trimmed = name[:-1] if name.endswith("/") else name
        path = PurePosixPath(trimmed)
        if not trimmed or any(part in {"", ".", ".."} for part in path.parts):
            raise PptxVisualRejected("PPTX contains an unsafe ZIP member path.")
        return path

    @staticmethod
    def _is_symlink(member: zipfile.ZipInfo) -> bool:
        return stat.S_ISLNK((member.external_attr >> 16) & 0xFFFF)

    @classmethod
    def _dangerous_member(cls, name: str) -> bool:
        lowered = name.lower()
        return (
            lowered.endswith("/vbaproject.bin")
            or lowered == "ppt/vbaproject.bin"
            or lowered.startswith("ppt/activex/")
            or lowered.startswith("ppt/embeddings/")
            or lowered.startswith("ppt/oleobjects/")
            or lowered.startswith("ppt/ctrlprops/")
        )

    @classmethod
    def _validate_relationships(cls, name: str, content: bytes) -> None:
        if len(content) > 2 * 1024 * 1024:
            raise PptxVisualRejected("PPTX relationship XML exceeds its limit.")
        lowered = content.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise PptxVisualRejected("PPTX relationship XML is unsafe.")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise PptxVisualRejected(
                f"PPTX relationship XML is invalid: {name}."
            ) from exc
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "Relationship":
                continue
            if element.attrib.get("TargetMode", "").lower() == "external":
                raise PptxVisualRejected(
                    "PPTX contains an external relationship and is unsafe."
                )

    @classmethod
    def _validate_package(cls, path: Path) -> int:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if not members or len(members) > cls.MAX_MEMBERS:
                    raise PptxVisualRejected("PPTX ZIP member count is unsafe.")
                total_expanded = 0
                seen: set[str] = set()
                slide_numbers: list[int] = []
                content_types: bytes | None = None
                for member in members:
                    safe_path = cls._safe_member_name(member.filename)
                    normalized = safe_path.as_posix()
                    identity = normalized.casefold()
                    if identity in seen:
                        raise PptxVisualRejected(
                            "PPTX contains duplicate ZIP member identities."
                        )
                    seen.add(identity)
                    if member.flag_bits & 0x1:
                        raise PptxVisualRejected("Encrypted PPTX members are unsafe.")
                    if cls._is_symlink(member):
                        raise PptxVisualRejected("PPTX contains an unsafe symlink.")
                    total_expanded += member.file_size
                    if (
                        member.file_size > cls.MAX_MEMBER_BYTES
                        or total_expanded > cls.MAX_EXPANDED_BYTES
                    ):
                        raise PptxVisualRejected("PPTX expanded content is unsafe.")
                    if (
                        member.file_size > 0
                        and member.compress_size == 0
                        or member.compress_size > 0
                        and member.file_size / member.compress_size
                        > cls.MAX_COMPRESSION_RATIO
                    ):
                        raise PptxVisualRejected("PPTX compression ratio is unsafe.")
                    if cls._dangerous_member(normalized):
                        raise PptxVisualRejected(
                            "PPTX contains unsafe macro, ActiveX, or embedded content."
                        )
                    match = cls._SLIDE_MEMBER.fullmatch(normalized)
                    if match:
                        slide_numbers.append(int(match.group(1)))
                    if normalized == "[Content_Types].xml":
                        content_types = archive.read(member)
                    if normalized.lower().endswith(".rels"):
                        cls._validate_relationships(
                            normalized, archive.read(member)
                        )
        except PptxVisualRejected:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise PptxVisualRejected("PPTX package is invalid or unreadable.") from exc
        if content_types is None or any(
            marker in content_types.lower()
            for marker in cls._DANGEROUS_CONTENT_TYPES
        ):
            raise PptxVisualRejected("PPTX content types are unsafe.")
        if not slide_numbers or sorted(slide_numbers) != list(
            range(1, len(slide_numbers) + 1)
        ):
            raise PptxVisualRejected("PPTX slide identities are invalid.")
        return len(slide_numbers)

    @staticmethod
    def _terminate_process(process: Any) -> None:
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            process.kill()
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    process.kill()
                try:
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def _convert(self, source: Path, output_dir: Path, profile: Path) -> Path:
        converter = self.locate_converter()
        if converter is None:
            raise PptxVisualUnavailable(
                "LibreOffice soffice is unavailable for PPTX visual previews."
            )
        arguments = [
            str(converter),
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        environment = {
            "HOME": str(profile.parent),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.pathsep.join(
                [str(converter.parent), "/usr/bin", "/bin"]
            ),
            "TMPDIR": str(profile.parent),
        }
        process: Any | None = None
        try:
            process = self.popen_factory(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(profile.parent),
                env=environment,
                start_new_session=True,
            )
            _, stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process(process)
            raise PptxVisualUnavailable(
                "PPTX visual rendering timed out."
            ) from exc
        except BaseException:
            self._terminate_process(process)
            raise
        if process.returncode != 0:
            detail = bytes(stderr or b"")[:500].decode("utf-8", errors="replace")
            raise PptxVisualUnavailable(
                "LibreOffice could not render this PPTX."
                + (f" Renderer detail: {detail}" if detail else "")
            )
        pdf_path = output_dir / f"{source.stem}.pdf"
        if pdf_path.is_symlink() or not pdf_path.is_file():
            raise PptxVisualUnavailable(
                "LibreOffice did not produce the expected PDF."
            )
        if pdf_path.stat().st_size <= 0 or pdf_path.stat().st_size > self.MAX_PDF_BYTES:
            raise PptxVisualRejected("Rendered PDF size is unsafe.")
        return pdf_path

    @classmethod
    def _validate_png(cls, path: Path, expected_width: int) -> tuple[int, int, int]:
        content = path.read_bytes()
        if len(content) < 24 or not content.startswith(cls._PNG_SIGNATURE):
            raise PptxVisualRejected("Rendered image is not a valid PNG.")
        width = int.from_bytes(content[16:20], "big")
        height = int.from_bytes(content[20:24], "big")
        if (
            width != expected_width
            or height <= 0
            or height > cls.MAX_OUTPUT_HEIGHT
        ):
            raise PptxVisualRejected("Rendered PNG dimensions are unsafe.")
        return width, height, len(content)

    def _rasterize(
        self, pdf_path: Path, slide_count: int, stage_dir: Path
    ) -> dict[int, Path]:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise PptxVisualUnavailable(
                "PDFium is unavailable for PPTX visual previews."
            ) from exc
        try:
            document = pdfium.PdfDocument(str(pdf_path))
        except Exception as exc:
            raise PptxVisualRejected("Rendered PDF is invalid.") from exc
        try:
            if len(document) != slide_count:
                raise PptxVisualRejected(
                    "Rendered PDF page count does not match the PPTX slide count."
                )
            output: dict[int, Path] = {}
            total_bytes = 0
            for index in range(slide_count):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if (
                        not math.isfinite(width)
                        or not math.isfinite(height)
                        or width <= 0
                        or height <= 0
                    ):
                        raise PptxVisualRejected(
                            "Rendered PDF page dimensions are unsafe."
                        )
                    scale = self.OUTPUT_WIDTH / width
                    expected_height = int(math.ceil(height * scale))
                    if expected_height <= 0 or expected_height > self.MAX_OUTPUT_HEIGHT:
                        raise PptxVisualRejected(
                            "Rendered PNG dimensions are unsafe."
                        )
                    bitmap = page.render(
                        scale=scale,
                        fill_color=(255, 255, 255, 255),
                    )
                    try:
                        image = bitmap.to_pil().convert("RGB")
                        target = stage_dir / f"slide-{index + 1:04d}.png"
                        image.save(
                            target,
                            format="PNG",
                            optimize=False,
                            compress_level=9,
                        )
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                _, _, image_bytes = self._validate_png(target, self.OUTPUT_WIDTH)
                if image_bytes > self.max_image_bytes:
                    raise PptxVisualRejected(
                        "Rendered PNG exceeds the image byte limit."
                    )
                total_bytes += image_bytes
                if total_bytes > self.max_total_bytes:
                    raise PptxVisualRejected(
                        "Rendered PNGs exceed the total byte limit."
                    )
                output[index + 1] = target
            return output
        finally:
            document.close()

    def render(
        self,
        path: Path,
        expected_sha256: str,
        output_dir: Path,
    ) -> dict[int, Path]:
        """Render a hash-bound safe PPTX to deterministic slide PNG paths."""

        source = Path(path)
        if source.suffix.lower() != ".pptx":
            raise PptxVisualRejected("Only .pptx visual rendering is supported.")
        if source.is_symlink() or not source.is_file():
            raise PptxVisualRejected("PPTX source must be a regular file.")
        if source.stat().st_size <= 0 or source.stat().st_size > self.MAX_SOURCE_BYTES:
            raise PptxVisualRejected("PPTX source size is unsafe.")
        if self._SHA256.fullmatch(expected_sha256) is None:
            raise PptxVisualRejected("Expected PPTX hash is invalid.")
        initial_hash = self._sha256_file(source)
        if not hmac.compare_digest(initial_hash, expected_sha256):
            raise PptxVisualRejected("PPTX source hash does not match.")
        slide_count = self._validate_package(source)

        destination = Path(output_dir)
        if destination.is_symlink():
            raise PptxVisualRejected("PPTX visual output cannot be a symlink.")
        destination.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or not destination.is_dir():
            raise PptxVisualRejected("PPTX visual output directory is unsafe.")

        with tempfile.TemporaryDirectory(prefix="course-pptx-visual-") as raw_temp:
            temp_root = Path(raw_temp)
            input_dir = temp_root / "input"
            converter_output = temp_root / "converted"
            stage_dir = temp_root / "rendered"
            profile = temp_root / "profile"
            for directory in (input_dir, converter_output, stage_dir, profile):
                directory.mkdir(mode=0o700)
            isolated_source = input_dir / "source.pptx"
            shutil.copyfile(source, isolated_source)
            if self._sha256_file(isolated_source) != expected_sha256:
                raise PptxVisualRejected("Copied PPTX source hash changed.")
            pdf_path = self._convert(isolated_source, converter_output, profile)
            staged = self._rasterize(pdf_path, slide_count, stage_dir)
            if self._sha256_file(source) != expected_sha256:
                raise PptxVisualRejected("PPTX source changed during rendering.")

            result: dict[int, Path] = {}
            for index, staged_path in staged.items():
                target = destination / staged_path.name
                if target.is_symlink():
                    raise PptxVisualRejected(
                        "PPTX visual output target is an unsafe symlink."
                    )
                temporary = destination / f".{staged_path.name}.tmp-{os.getpid()}"
                try:
                    if temporary.exists() or temporary.is_symlink():
                        raise PptxVisualRejected(
                            "PPTX visual output staging path is unsafe."
                        )
                    shutil.copyfile(staged_path, temporary)
                    os.replace(temporary, target)
                finally:
                    if temporary.exists() and not temporary.is_symlink():
                        temporary.unlink()
                result[index] = target
            return result


__all__ = [
    "PptxVisualRejected",
    "PptxVisualRenderer",
    "PptxVisualUnavailable",
]
