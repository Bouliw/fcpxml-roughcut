#!/usr/bin/env python3
"""Sub-pixel subtle Ken Burns: still image -> silent mp4. NO ffmpeg zoompan (integer-stepped = shaky).
Usage: zoom_still.py in.png out.mp4 DURATION [--rate 0.015] [--fps 30|29.97|30000/1001] [--size 1920x1080]
                     [--blurfill] [--overlay credit.png]
--blurfill: fit the image at natural scale over a blurred copy (for portrait/odd aspect, e.g. tweets).
--rate 0: fully static (the profile's opt-out).
--overlay: a transparent PNG of the frame size laid on top, unzoomed (the source credit)."""
import subprocess, sys
from fractions import Fraction
from PIL import Image, ImageFilter

NTSC = {'23.976': Fraction(24000, 1001), '29.97': Fraction(30000, 1001), '59.94': Fraction(60000, 1001)}

def parse_fps(text):
    """'25', '29.97' or '30000/1001' -> exact Fraction."""
    text = str(text)
    return NTSC.get(text) or (Fraction(text) if '/' in text else Fraction(text).limit_denominator(1001))

def cover(im, W, H):
    sw, sh = im.size; sc = max(W/sw, H/sh)
    im = im.resize((round(sw*sc), round(sh*sc)), Image.LANCZOS)
    x0, y0 = (im.width-W)//2, (im.height-H)//2
    return im.crop((x0, y0, x0+W, y0+H))

def render(inp, out, dur, rate=0.015, fps=Fraction(30), size=(1920, 1080), blur=False, overlay=None):
    W, H = size
    src = Image.open(inp).convert("RGB")
    if blur:
        bg = cover(src, W, H).filter(ImageFilter.GaussianBlur(45))
        sc = min(W*0.88/src.width, H*0.88/src.height)
        fg = src.resize((round(src.width*sc), round(src.height*sc)), Image.LANCZOS)
        bg.paste(fg, ((W-fg.width)//2, (H-fg.height)//2)); base = bg
    else:
        base = cover(src, W, H)
    n = round(dur*fps)
    rate_s = f'{fps.numerator}/{fps.denominator}'
    cmd = ["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",rate_s,"-i","-"]
    if overlay:
        cmd += ["-i", overlay, "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]", "-map", "[v]"]
    cmd += ["-an","-c:v","libx264","-crf","16","-preset","slow","-pix_fmt","yuv420p",
            "-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709","-movflags","+faststart",out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(n):
        s = 1.0 + rate*float(f/fps)
        bw, bh = W/s, H/s
        x0, y0 = (W-bw)/2, (H-bh)/2
        ff.stdin.write(base.resize((W,H), Image.LANCZOS, box=(x0,y0,x0+bw,y0+bh)).tobytes())
    ff.stdin.close()
    if ff.wait():
        raise SystemExit(f"ffmpeg failed on {out}")
    return out

if __name__ == "__main__":
    args = sys.argv[1:]
    blur = "--blurfill" in args
    args = [a for a in args if a != "--blurfill"]
    def opt(name, default):
        return args[args.index(name)+1] if name in args else default
    W, H = map(int, opt("--size", "1920x1080").split("x"))
    render(args[0], args[1], float(args[2]), float(opt("--rate", 0.015)), parse_fps(opt("--fps", "30")),
           (W, H), blur, opt("--overlay", None))
    print("done", args[1])
