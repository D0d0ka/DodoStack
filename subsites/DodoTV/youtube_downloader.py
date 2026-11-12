import yt_dlp

def download_video(url):
    print("\nSubtitle language selection:")
    print("1. All languages")
    print("2. Only one language")

    choice = input("Valik (1/2): ").strip()

    if choice == "1":
        subtitles_langs = ['all']
    elif choice == "2":
        lang = input("Enter subtitle language code (nt et, en, de): ").strip()
        if not lang:
            print("Language code is missing!")
            return
        subtitles_langs = [lang]
    else:
        print("Invalid choice!")
        return

    options = {
        # Save location
        'outtmpl': 'downloads/%(title)s/%(title)s.%(ext)s',

        # Video quality
        'format': 'bestvideo+bestaudio/best',

        # Subtitles
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': subtitles_langs,
        'subtitlesformat': 'vtt/best',

        # Thumbnail
        'writethumbnail': True,
        'postprocessors': [
            {
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'jpg',
            }
        ],

        # Other settings
        'noplaylist': True,
        'progress': True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        print("\nDownload complete! 🦤")

    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    print("=== YouTube Downloader 🦤 ===")

    url = input("Enter YouTube video URL: ").strip()

    if url:
        download_video(url)
    else:
        print("URL is missing!")