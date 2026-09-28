# Third-party notices

Roughcut and the fcpxml-roughcut scripts are under the MIT license (see [LICENSE](LICENSE)). The engine's tools,
which the app downloads at its first launch (`Roughcut-engine-1-arm64.tar.gz`, from the release named in
`app/runtime/versions.sh`), are other people's work, each under its own license. The full license texts, and the
exact sources and configuration they were built from, are in the archive's `licenses` folder, installed at
`~/Library/Application Support/Roughcut/engine-1/licenses`. `app/runtime/build_runtime.sh` rebuilds them all from
those sources.

| Component | Version | License | Source |
|---|---|---|---|
| FFmpeg (ffmpeg, ffprobe) | 8.1.3 | LGPL 2.1 or later, built without any GPL or non-free part | https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz |
| FreeType | 2.14.3 | FreeType License (FTL) | https://download.savannah.gnu.org/releases/freetype/ |
| GNU FriBidi | 1.0.17 | LGPL 2.1 or later | https://github.com/fribidi/fribidi |
| HarfBuzz | 14.5.0 | MIT (Old MIT) | https://github.com/harfbuzz/harfbuzz |
| libass | 0.17.5 | ISC | https://github.com/libass/libass |
| whisper.cpp and ggml | 1.9.4 | MIT | https://github.com/ggml-org/whisper.cpp |
| Whisper models (large-v3-turbo, large-v3) | | MIT (OpenAI), downloaded from Hugging Face, not redistributed | https://huggingface.co/ggerganov/whisper.cpp |
| Python (python-build-standalone) | 3.12.14 | PSF License 2.0, and the licenses of the libraries built into it | https://github.com/astral-sh/python-build-standalone |
| NumPy | 2.5.3 | BSD-3-Clause (with 0BSD, MIT, Zlib and CC0 parts) | https://numpy.org |
| PyObjC (core, Cocoa, Quartz, CoreML, Vision) | 12.2.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| Anthropic Python SDK | 1.8.0 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| httpx2, httpcore2, idna | 2.13.1, 2.13.1, 3.20 | BSD-3-Clause | PyPI |
| anyio, h11, jiter, pydantic, pydantic-core, annotated-types, docstring-parser, truststore, typing-inspection | | MIT | PyPI |
| sniffio | 1.3.1 | MIT or Apache 2.0 | PyPI |
| typing-extensions | 4.16.0 | PSF 2.0 | PyPI |

Portions of this software are copyright © 1996-2026 The FreeType Project (https://freetype.org). All rights reserved.

FFmpeg and FriBidi are used under the GNU Lesser General Public License 2.1 or later: they are separate programs (FriBidi
inside ffmpeg), their sources are listed above, and anyone may rebuild them or replace the ones in
`~/Library/Application Support/Roughcut/engine-1/bin`.

The app itself uses only Apple's frameworks (SwiftUI, AppKit, AVFoundation, UserNotifications, Security, CryptoKit).
