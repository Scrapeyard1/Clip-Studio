"""Editable timeline export: FCP7 XML (xmeml v5).

One file opens in BOTH DaVinci Resolve (File > Import > Timeline) and Premiere Pro
(File > Import). Every cut points back at the ORIGINAL recording, so the editor can
drag any edge to restore a trimmed pause, swap a cold-open clip, or re-cut by hand.

Layout of the exported sequence:
  V1           cold open clips | sting | conversation (one clip per kept stretch) | outro
  A1, A2, ...  one audio track per audio track in the recording (OBS: mix, mic, guest...)
  markers      "Cold open", "Conversation", and every removed pause (so nothing is hidden)
"""
import json, os, subprocess, urllib.parse
from xml.sax.saxutils import escape


def probe(path):
    out = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,width,height,r_frame_rate,channels,sample_rate",
         "-show_entries", "format=duration", "-of", "json", path]))
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    a = [s for s in out["streams"] if s["codec_type"] == "audio"]
    n, d = map(int, v["r_frame_rate"].split("/"))
    return {"w": v["width"], "h": v["height"], "fps": n / d, "dur": float(out["format"]["duration"]),
            "audio": [{"ch": s.get("channels", 2), "sr": int(s.get("sample_rate", 48000))} for s in a]}


def _rate(fps):
    ntsc = abs(fps - round(fps)) > 0.01
    return f"<rate><timebase>{round(fps)}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def pathurl(p):
    p = os.path.abspath(p).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p                     # Windows: /C:/Lives/...
    return "file://localhost" + urllib.parse.quote(p)


def _file(fid, path, info, full):
    if not full:
        return f'<file id="{fid}"/>'
    fr = round(info["fps"])
    aud = "".join(f"<audio><samplecharacteristics><depth>16</depth><samplerate>{a['sr']}</samplerate>"
                  f"</samplecharacteristics><channelcount>{a['ch']}</channelcount></audio>" for a in info["audio"])
    return (f'<file id="{fid}"><name>{escape(os.path.basename(path))}</name><pathurl>{pathurl(path)}</pathurl>'
            f'{_rate(info["fps"])}<duration>{int(info["dur"] * fr)}</duration>'
            f'<media><video><samplecharacteristics>{_rate(info["fps"])}<width>{info["w"]}</width>'
            f'<height>{info["h"]}</height></samplecharacteristics></video>{aud}</media></file>')


def build(out_path, name, video, segments, extra_files=None, markers=()):
    """segments: ordered [{"src": "main"|"sting"|"outro", "in": s, "out": s, "label": str}] in seconds.
    extra_files: {"sting": path, "outro": path} (optional)."""
    files = {"main": video, **(extra_files or {})}
    info = {k: probe(p) for k, p in files.items()}
    main = info["main"]
    fr = round(main["fps"])
    used = set()
    vclips, aclips = [], {}
    t = 0
    n = 0
    for seg in segments:
        src = seg["src"]
        fi = info[src]
        fin, fout = int(round(seg["in"] * fr)), int(round(seg["out"] * fr))
        if fout <= fin:
            continue
        dur = fout - fin
        n += 1
        fid = f"file-{src}"
        full = fid not in used
        used.add(fid)
        vid = f"clipitem-v{n}"
        links = [f"<link><linkclipref>{vid}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex>"
                 f"<clipindex>{n}</clipindex></link>"]
        for ai in range(len(fi["audio"])):
            links.append(f"<link><linkclipref>clipitem-a{ai + 1}-{n}</linkclipref><mediatype>audio</mediatype>"
                         f"<trackindex>{ai + 1}</trackindex><clipindex>{n}</clipindex></link>")
        common = (f"<name>{escape(seg.get('label', os.path.basename(files[src])))}</name><enabled>TRUE</enabled>"
                  f"<duration>{int(fi['dur'] * fr)}</duration>{_rate(main['fps'])}"
                  f"<start>{t}</start><end>{t + dur}</end><in>{fin}</in><out>{fout}</out>")
        vclips.append(f'<clipitem id="{vid}">{common}{_file(fid, files[src], fi, full)}{"".join(links)}</clipitem>')
        for ai in range(len(fi["audio"])):
            aclips.setdefault(ai, []).append(
                f'<clipitem id="clipitem-a{ai + 1}-{n}">{common}<file id="{fid}"/>'
                f"<sourcetrack><mediatype>audio</mediatype><trackindex>{ai + 1}</trackindex></sourcetrack>"
                f'{"".join(links)}</clipitem>')
        t += dur
    mk = "".join(f"<marker><name>{escape(m)}</name><comment></comment><in>{int(round(s * fr))}</in><out>-1</out></marker>"
                 for s, m in markers)
    atracks = "".join(f"<track>{''.join(c)}<outputchannelindex>{(i % 2) + 1}</outputchannelindex></track>"
                      for i, c in sorted(aclips.items()))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
           f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{t}</duration>{_rate(main["fps"])}'
           f"<timecode>{_rate(main['fps'])}<string>01:00:00:00</string><frame>{3600 * fr}</frame>"
           f"<displayformat>NDF</displayformat></timecode>"
           f"<media><video><format><samplecharacteristics>{_rate(main['fps'])}<width>{main['w']}</width>"
           f"<height>{main['h']}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics>"
           f"</format><track>{''.join(vclips)}</track></video>"
           f"<audio><numOutputChannels>2</numOutputChannels>{atracks}</audio></media>{mk}</sequence></xmeml>\n")
    open(out_path, "w", encoding="utf-8").write(xml)
    return t / fr
