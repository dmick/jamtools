from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, HTMLResponse

from lyrics_utils import do_fetch_setlist, format_setlist, input_form
import set_utils

from sqlmodel import SQLModel, create_engine, Field, Session, select
from contextlib import asynccontextmanager
import config

import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    stream=sys.stderr,
    format='%(asctime)s %(name)s:%(levelname)s %(message)s'

)
log = logging.getLogger(__name__)

class Lyrics(SQLModel, table=True):
    song: str = Field(primary_key=True)
    artist: str = Field(primary_key=True)
    lyrics: str

sqlite_url = f"sqlite:///{config.SQLITE_FILE}"

engine = create_engine(sqlite_url)

# arrange for the DB to be created on app startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)

@app.get('/lyrics')
async def do_lyrics(
    setlist: str|None = None,
    date: str|None = None,
    html: str|None = None,
    sheetid: str|None = None,
    seq: str|None = None,
    ) -> Response:

    dohtml:bool = html is not None
    doseq:bool = seq is not None

    if not (setlist or date or sheetid):
        response = HTMLResponse(input_form('/lyrics', dateonly=False))
        return response

    set_with_lyrics: list[dict] = []
    if date or sheetid:
        rows = set_utils.find_set(sheetid, None, date)
        if not rows:
            dialog = f'<script>alert("Oops, no set found for {date}")</script>'
            return HTMLResponse(content=dialog)
        set_with_lyrics = [{'song' :r['song'], 'artist': r['artist'], 'lyrics':None} for r in rows]
    elif setlist:
        setlist_lines = setlist.split('\n')
        for sl in setlist_lines:
            sl = sl.strip()
            if len(sl) == 0:
                continue
            log.info(f'setlist input: {sl}')
            song, artist = sl.split(',')
            set_with_lyrics.append({'song':song, 'artist':artist, 'lyrics': None})

    # load any cached lyrics
    with Session(engine) as session:
        for row in set_with_lyrics:
            song, artist = row['song'], row['artist']
            results = session.exec(
                select(Lyrics).where(
                    Lyrics.song == song and
                    Lyrics.artist == artist
                )
            )
            if newlyrobj := results.one_or_none():
                log.info(f'found cached lyrics for {song}, {artist}')
                row['lyrics'] = newlyrobj.lyrics

    if all([r["lyrics"] is not None for r in set_with_lyrics]):
        log.info("all lyrics cached, skipping fetch")
        failures = None
        fetched_set = set_with_lyrics
    else:
        failures, fetched_set = do_fetch_setlist(set_with_lyrics)

        # save any lyrics we just got
        with Session(engine) as session:
            for row, newrow in zip(set_with_lyrics, fetched_set):
                if row['lyrics'] is None and newrow['lyrics'] is not None:
                    log.info(f'got new lyrics for {newrow["song"]} {newrow["artist"]}')
                    session.add(Lyrics(
                        song=newrow['song'],
                        artist=newrow['artist'],
                        lyrics=newrow['lyrics']
                    ))
            session.commit()

    formatted_lyrics = format_setlist(fetched_set, dohtml)

    if failures:
        failures = '\\n'.join((['NOT_FOUND:\\n'] + failures))
        dialog = f'<script>alert("{failures}")</script>'
    else:
        dialog = ''
    response = HTMLResponse(content=dialog + formatted_lyrics)
    response.charset = 'utf-8'
    return response


@app.get('/setlist')
async def do_setlist(
    date: str|None = None,
    sheetid: str|None = None,
    ) -> Response:

    if not (date or sheetid):
        return HTMLResponse(input_form('/setlist', dateonly=True))

    rows = set_utils.find_set(sheetid, None, date)
    if not rows:
        dialog = f'<script>alert("Oops, no set found for {date}")</script>'
        return HTMLResponse(content=dialog)

    def poss_quote(s):
        if ',' in s:
            return f'"{s}"'
        return s

    setlist = [
        f'{poss_quote(row.get("song"))},{poss_quote(row.get("artist"))}' for row in rows
    ]
    setlist.append('')
    setlist.extend([f'{row.get("artist")} - {row.get("song")}' for row in rows])
    setlist.append('')

    response = PlainTextResponse(content='\n'.join(setlist))
    response.charset = 'utf-8'
    return response
