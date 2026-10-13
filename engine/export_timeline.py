"""Editable timeline export: FCP7 XML (xmeml v5).

One file opens in BOTH DaVinci Resolve (File > Import > Timeline) and Premiere Pro
(File > Import). Every cut points back at the ORIGINAL recording, so the editor can
drag any edge to restore a trimmed pause, swap a cold-open clip, or re-cut by hand.

Built from the same Timeline as the rendered video (engine/timeline.py), so its cut points,
length and markers match the video frame for frame, at the recording's exact frame rate.

Layout:
  V1           cold open clips | sting | conversation (one clip per kept stretch) | outro
  A1, A2, ...  one audio track per audio track in the recording (OBS: mix, mic, guest...)
  markers      "Cold open", "Conversation starts", and every trimmed pause
"""
import os
import urllib.parse
from fractions import Fraction
from xml.sax.saxutils import escape

import media


def rate_xml(fps):
    """xmeml rate: integer timebase + NTSC flag. 30000/1001 -> 30 + TRUE (frames are still real frames)."""
    fps = Fraction(fps)
    tb = round(fps)
    ntsc = fps.denominator == 1001 or abs(float(fps) - tb) > 1e-6
    return f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def pathurl(p):
    p = os.path.abspath(p).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p                     # Windows: /C:/Lives/...
    return "file://localhost" + urllib.parse.quote(p)


def _file_xml(fid, path, info, fps):
    aud = "".join(f"<audio><samplecharacteristics><depth>16</depth><samplerate>{a['rate']}</samplerate>"
                  f"</samplecharacteristics><channelcount>{a['channels']}</channelcount></audio>" for a in info["audio"])
    vid = ""
    if "fps" in info:
        vid = (f"<video><samplecharacteristics>{rate_xml(info['fps'])}<width>{info['width']}</width>"
               f"<height>{info['height']}</height></samplecharacteristics></video>")
    dur = round(Fraction(info["duration"]) * Fraction(fps))
    return (f'<file id="{fid}"><name>{escape(os.path.basename(path))}</name><pathurl>{pathurl(path)}</pathurl>'
            f"{rate_xml(fps)}<duration>{dur}</duration><media>{vid}{aud}</media></file>")


def build(out_path, name, timeline, files):
    """timeline: engine.timeline.Timeline. files: {"main": recording, "sting": path, "outro": path}.
    Segments whose src has no real file (placeholder cards) become gaps the editor can fill."""
    fps = timeline.fps
    info = {k: media.probe(p) for k, p in files.items() if p and not str(k).startswith("card")}
    main = info["main"]
    declared, vclips, aclips = set(), [], {}
    n = 0
    for seg in timeline.segments:
        src = seg["src"]
        if src not in info:
            continue                      # placeholder card: leave a gap of the right length
        n += 1
        fi = info[src]
        fid = f"file-{src}"
        file_xml = _file_xml(fid, files[src], fi, fps) if fid not in declared else f'<file id="{fid}"/>'
        declared.add(fid)
        links = [f"<link><linkclipref>clipitem-v{n}</linkclipref><mediatype>video</mediatype>"
                 f"<trackindex>1</trackindex><clipindex>{n}</clipindex></link>"]
        links += [f"<link><linkclipref>clipitem-a{ai + 1}-{n}</linkclipref><mediatype>audio</mediatype>"
                  f"<trackindex>{ai + 1}</trackindex><clipindex>{n}</clipindex></link>" for ai in range(len(fi["audio"]))]
        start, end = seg["at_f"], seg["at_f"] + seg["out_f"] - seg["in_f"]
        common = (f"<name>{escape(seg.get('label') or name)}</name><enabled>TRUE</enabled>"
                  f"<duration>{round(Fraction(fi['duration']) * fps)}</duration>{rate_xml(fps)}"
                  f"<start>{start}</start><end>{end}</end><in>{seg['in_f']}</in><out>{seg['out_f']}</out>")
        vclips.append(f'<clipitem id="clipitem-v{n}">{common}{file_xml}{"".join(links)}</clipitem>')
        for ai in range(len(fi["audio"])):
            aclips.setdefault(ai, []).append(
                f'<clipitem id="clipitem-a{ai + 1}-{n}">{common}<file id="{fid}"/>'
                f"<sourcetrack><mediatype>audio</mediatype><trackindex>{ai + 1}</trackindex></sourcetrack>"
                f'{"".join(links)}</clipitem>')
    marks = [(0, "Cold open")]
    body = [s for s in timeline.segments if s["kind"] == "body"]
    if body:
        marks.append((body[0]["at_f"], "Conversation starts"))
    for prev, nxt in zip(body, body[1:]):
        gap = nxt["in_f"] - prev["out_f"]
        if gap > 0:
            marks.append((nxt["at_f"], f"Trimmed {timeline.sec(gap):.1f}s pause - drag edge to restore"))
    for s in timeline.segments:
        if s["src"].startswith("card"):
            marks.append((s["at_f"], f"{s['label']} goes here (set its file in Settings)"))
    mk = "".join(f"<marker><name>{escape(m)}</name><comment></comment><in>{f}</in><out>-1</out></marker>"
                 for f, m in marks)
    atracks = "".join(f"<track>{''.join(c)}<outputchannelindex>{(i % 2) + 1}</outputchannelindex></track>"
                      for i, c in sorted(aclips.items()))
    tb = round(fps)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
           f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{timeline.frames}</duration>{rate_xml(fps)}'
           f"<timecode>{rate_xml(fps)}<string>01:00:00:00</string><frame>{3600 * tb}</frame>"
           f"<displayformat>NDF</displayformat></timecode>"
           f"<media><video><format><samplecharacteristics>{rate_xml(fps)}<width>{main['width']}</width>"
           f"<height>{main['height']}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics>"
           f"</format><track>{''.join(vclips)}</track></video>"
           f"<audio><numOutputChannels>2</numOutputChannels>{atracks}</audio></media>{mk}</sequence></xmeml>\n")
    tmp = media.tmp_name(out_path)
    open(tmp, "w", encoding="utf-8").write(xml)
    return tmp
