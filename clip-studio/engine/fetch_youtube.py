"""Get a recording from a YouTube link (meant for your own channel's videos and live replays).

Uses yt-dlp, the standard open-source downloader. Takes the best video + best audio YouTube
has and merges them into one MP4 with FFmpeg.

Quality note: YouTube's copy of a live is re-compressed and has ONE mixed audio track, so the
local OBS recording is still better (cleaner picture, separate mic/guest tracks). The link is
for when the local file isn't handy.

YouTube changes things often; when downloads start failing, `pip install -U yt-dlp` fixes it.
"""
import os
import yt_dlp


class FetchError(Exception):
    pass


def info(url):
    """Title, date, length and whether it's still live, without downloading."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        try:
            i = y.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            raise FetchError(_friendly(str(e)))
    if i.get("is_live") or i.get("live_status") in ("is_live", "is_upcoming"):
        raise FetchError("That live hasn't finished yet. Add it once it has ended and YouTube has processed the replay.")
    return {"title": i.get("title") or "YouTube video", "date": i.get("upload_date"),
            "duration": i.get("duration"), "channel": i.get("channel") or i.get("uploader"), "id": i.get("id")}


def download(url, dest_dir, on_progress=None):
    """Downloads to dest_dir and returns the file path. on_progress(percent) is called as it goes."""
    os.makedirs(dest_dir, exist_ok=True)
    last = [-1]

    def hook(d):
        if d["status"] == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = int(100 * d.get("downloaded_bytes", 0) / total)
                if pct != last[0]:
                    last[0] = pct
                    on_progress(pct)
    opts = {
        "format": "bv*+ba/b",                   # best video + best audio, or best single file
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(dest_dir, "%(upload_date|)s %(title).80B [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True, "no_warnings": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
        "retries": 5, "fragment_retries": 10,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        try:
            i = y.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise FetchError(_friendly(str(e)))
        path = y.prepare_filename(i)
    base = os.path.splitext(path)[0]
    for ext in (".mp4", ".mkv", ".webm", os.path.splitext(path)[1]):
        if os.path.exists(base + ext):
            return base + ext
    raise FetchError("The download finished but the file wasn't where expected. Try again.")


def _friendly(msg):
    m = msg.lower()
    if "private" in m or "sign in" in m or "members-only" in m:
        return "YouTube wants a sign-in for that video (private, members-only or age-restricted). Set it to unlisted, or use the local file."
    if "unsupported url" in m or "is not a valid url" in m:
        return "That doesn't look like a video link. Copy the link from the video's Share button and paste it again."
    if "unavailable" in m or "removed" in m:
        return "YouTube says that video isn't available."
    if "unable to connect" in m or "proxy" in m or "timed out" in m or "getaddrinfo" in m or "name resolution" in m:
        return "Couldn't reach YouTube. Check this computer's internet connection and try again."
    if "http error 403" in m or "sabr" in m or "signature" in m or "nsig" in m:
        return "YouTube blocked the download. Update the downloader (pip install -U yt-dlp) and try again."
    return "The download didn't work: " + msg.split("ERROR:")[-1].strip()[:200]
