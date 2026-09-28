# Sources of the engine runtime: pinned versions and SHA-256 checksums (build_runtime.sh checks every download).
# A runtime is published once, with the release named here; the apps that follow download it from there and check it
# against RUNTIME_SHA. A new runtime: bump RUNTIME_VERSION, build it, publish it with a release, update both lines.
RUNTIME_VERSION=1
RUNTIME_RELEASE=v0.1.0
RUNTIME_SHA=5edac7f0913355ea642669d11b2c7e0ee6f3315b6cba7e51163542b7c9f59c56
PYTHON_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.12.14+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz
PYTHON_SHA=c2edb321cd32ec2b170df208db0446dccc4398db602ca27cf2079098fb1f7d9d
FFMPEG_URL=https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz
FFMPEG_SHA=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
FREETYPE_URL=https://download.savannah.gnu.org/releases/freetype/freetype-2.14.3.tar.xz
FREETYPE_SHA=36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f
FRIBIDI_URL=https://github.com/fribidi/fribidi/releases/download/v1.0.17/fribidi-1.0.17.tar.xz
FRIBIDI_SHA=6949dcde27d41cebad1fd741fcafc36d55a1020d2d872d4a6eb3914caabbada2
HARFBUZZ_URL=https://github.com/harfbuzz/harfbuzz/releases/download/14.5.0/harfbuzz-14.5.0.tar.xz
HARFBUZZ_SHA=b7132e148358a45185c9feafd049dbaf243649d3c44414b3534d9c95d18592b9
LIBASS_URL=https://github.com/libass/libass/releases/download/0.17.5/libass-0.17.5.tar.xz
LIBASS_SHA=2dca25c0e0c837ddf00b52011b3f82cac1e4ddd3ad018227806b0c2288864acc
WHISPER_URL=https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.4.tar.gz
WHISPER_SHA=57e280cee375ab02425b806ad5146b99f6eb9357e3c2b31357c8a6af2e2e44ae
