import os
import config
import sys
import json
import requests
import jq
import argparse
from argparse import RawTextHelpFormatter
from transmission_rpc import Client

c = config.Config()
if not c.check("tautulliAPIkey", "radarrAPIkey"):
    print("ERROR: Required Tautulli/Radarr API key not set. Cannot continue.")
    sys.exit(1)

c.apicheck(c.radarrHost, c.radarrAPIkey)

parser = argparse.ArgumentParser(
    description="Enter a movie title as an argument to delete a movie from overseerr, radarr, and from the disk.\nDon't worry! You'll be prompted before it does a delete.\nSo that it is properly read, pass your title as:\n\n  --title=\"Search Title\"\n",
    formatter_class=RawTextHelpFormatter,
)
parser.add_argument(
    "--title",
    metavar="search title",
    type=str,
    nargs="?",
    help="The title to search for deletion.",
    required=True,
)
args = parser.parse_args()
if not isinstance(args.title, str) or len(args.title) < 1:
    parser.print_help(sys.stderr)
    sys.exit(1)


def purge(movie):
    deletesize = 0
    tmdbid = None

    r = requests.get(
        f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_metadata&rating_key={movie['rating_key']}"
    )

    guids = jq.compile(".[].data.guids").input(r.json()).first()

    try:
        if guids:
            tmdbid = [i for i in guids if i.startswith("tmdb://")][0].split(
                "tmdb://", 1
            )[1]
    except Exception as e:
        print(
            f"WARNING: {movie['title']}: Unexpected GUID metadata from Tautulli. Please refresh your library's metadata in Plex. Using less-accurate 'search mode' for this title. Error message: "
            + str(e)
        )
        guids = []

    f = requests.get(f"{c.radarrHost}/api/v3/movie?apiKey={c.radarrAPIkey}")
    try:
        if guids:
            radarr = (
                jq.compile(f".[] | select(.tmdbId == {tmdbid})").input(f.json()).first()
            )
        else:
            radarr = (
                jq.compile(f".[] | select(.title == \"{movie['title']}\")")
                .input(f.json())
                .first()
            )
        if not c.dryrun:
            response = requests.delete(
                f"{c.radarrHost}/api/v3/movie/"
                + str(radarr["id"])
                + f"?apiKey={c.radarrAPIkey}&deleteFiles=true"
            )

        try:
            if not c.dryrun and c.overseerrAPIkey is not None:
                headers = {"X-Api-Key": f"{c.overseerrAPIkey}"}
                o = requests.get(
                    f"{c.overseerrHost}/api/v1/movie/" + str(radarr["tmdbId"]),
                    headers=headers,
                )
                overseerr = json.loads(o.text)
                o = requests.delete(
                    f"{c.overseerrHost}/api/v1/media/"
                    + str(overseerr["mediaInfo"]["id"]),
                    headers=headers,
                )
        except Exception as e:
            print("ERROR: Unable to connect to overseerr.")

        # Transmission
        try:
            if c.transmissionRpcHost is not None:
                transmissionRemoteClient = Client(
                  protocol=str(c.transmissionRpcProtocol),
                  username=str(c.transmissionRemoteUser),
                  password=str(c.transmissionRemotePass),
                  host=str(c.transmissionRpcHost),
                  port=str(c.transmissionRpcPort),
                  path=str(c.transmissionRemotePath)
                )

                torrents = transmissionRemoteClient.get_torrents()
                torrentlist = []

                # Add torrent names and their corresponding IDs to a list
                for torrent in torrents:
                    torrentlist.append(torrent.name)
                    torrentlist.append(torrent.id)

                if radarr["movieFile"]["sceneName"] in torrentlist:
                  print("Torrent match found: " + radarr["movieFile"]["sceneName"])

                  # Print torrent info by its ID
                  torrent = transmissionRemoteClient.get_torrent(torrentlist[torrentlist.index(radarr["movieFile"]["sceneName"])+1])
                  print("Torrent ID: " + str(torrent.id))

                if not c.dryrun:
                  print("Removing torrent and its data: " + radarr["movieFile"]["sceneName"])
                  transmissionRemoteClient.remove_torrent(torrent.id, delete_data=True)

        except Exception as e:
            #print("ERROR: " + str(e))
            print("No original Radarr filename found for: " + movie["title"])

        action = "DELETED"
        if c.dryrun:
            action = "DRY RUN"

        print(
            action
            + ": "
            + movie["title"]
            + " | Radarr ID: "
            + str(radarr["id"])
            + " | TMDB ID: "
            + str(radarr["tmdbId"])
        )
        deletesize = int(movie["file_size"]) / 1073741824
    except StopIteration:
        pass
    except Exception as e:
        print("ERROR: " + movie["title"] + ": " + str(e))

    return deletesize


totalsize = 0
r = requests.get(
    f"{c.tautulliHost}/api/v2/?apikey={c.tautulliAPIkey}&cmd=get_library_media_info&section_id={c.tautulliMovieSectionID}&search={args.title}&refresh=true"
)
movies = json.loads(r.text)

try:
    if len(movies["response"]["data"]["data"]) == 1:
        movie = movies["response"]["data"]["data"][0]
        confirmation = input(
            "Movie found:\n"
            + movie["title"]
            + " ("
            + movie["year"]
            + ")\nDelete it? [N]: "
        )
        if confirmation.lower() == "y":
            confirmation = 1
        else:
            confirmation = 0
    elif len(movies["response"]["data"]["data"]) > 1:
        print("[0] Delete nothing")
        for i, movie in enumerate(movies["response"]["data"]["data"], 1):
            print("[" + str(i) + "] " + movie["title"] + " (" + movie["year"] + ")")
        try:
            if c.dryrun:
                print("DRY RUN MODE - no selected movies will be deleted")
            else:
                print("*** The selected movie will be deleted ***")
            confirmation = int(input("Choose a movie to delete [0]: "))
        except:
            print("No action taken.")
            sys.exit(0)
    else:
        print("I couldn't find your movie. Try a different search term.")
        sys.exit(0)

    if confirmation > 0:
        try:
            confirmation = confirmation - 1
            movie = movies["response"]["data"]["data"][confirmation]
            print("Total space reclaimed: " + str("{:.2f}".format(purge(movie))) + "GB")
        except Exception as e:
            print("Couldn't delete movie.\n\n" + str(e))
    else:
        print("No action taken.")
except Exception as e:
    print(
        "ERROR: There was a problem connecting to Tautulli/Radarr/Overseerr. Please double-check that your connection settings and API keys are correct.\n\nError message:\n"
        + str(e)
    )
    sys.exit(1)
