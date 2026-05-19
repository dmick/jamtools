from datetime import datetime
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, HTMLResponse

from lyrics_utils import do_fetch_setlist, format_setlist, input_form, fetch_override
import set_utils

from sqlmodel import SQLModel, create_engine, Field, Session, select
from typing import Optional
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
    mtime: Optional[datetime] = Field(default=None)
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
    nocache: str|None = None,
    ) -> Response:

    dohtml:bool = html is not None
    nocache:bool = nocache is not None

    if not (setlist or date or sheetid):
        response = HTMLResponse(input_form('/lyrics', dateonly=False))
        return response

    set_with_lyrics: list[dict] = []
    if date or sheetid:
        rows = set_utils.find_set(sheetid, None, date)
        if not rows:
            dialog = f'<script>alert("Oops, no set found for {date}")</script>'
            return HTMLResponse(content=dialog)
        set_with_lyrics = [{'song' :r.get('song'), 'artist': r.get('artist'), 'lyrics':None} for r in rows]
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
    if not nocache:
        with Session(engine) as session:
            for row in set_with_lyrics:
                song, artist = row.get('song'), row.get('artist')
                if song and artist:
                    results = session.exec(
                        select(Lyrics).where(
                            Lyrics.song == song and
                            Lyrics.artist == artist
                        )
                    )
                    if db_obj := results.one_or_none():
                        log.info(f'found db lyrics for {song}, {artist}')
                        row['lyrics'] = db_obj.lyrics

                    # whether or not we found it, look for overrides
                    mtime, override_lyrics = fetch_override(song, artist)
                    if override_lyrics and (
                        (not db_obj or not db_obj.lyrics) or
                        (mtime > db_obj.mtime)
                        ):
                        log.info(f'replacing {song}, {artist} with override lyrics from {mtime.isoformat()}')
                        new_obj = Lyrics(
                            song=song,
                            artist=artist,
                            lyrics=override_lyrics,
                            mtime = mtime)
                        row['lyrics'] = override_lyrics
                        session.merge(new_obj)
                        session.commit()
    else:
        log.info(f'Nocache set, not using db')

    # fetch any that are not already set
    failures, fetched_set = do_fetch_setlist(set_with_lyrics)

    # save any lyrics we just fetched
    with Session(engine) as session:
        for row, newrow in zip(set_with_lyrics, fetched_set):
            if row.get('lyrics') is None and newrow.get('lyrics') is not None:
                log.info(f'got new lyrics for {newrow["song"]} {newrow["artist"]}')
                obj = Lyrics(
                    song=newrow['song'],
                    artist=newrow['artist'],
                    lyrics=newrow['lyrics'],
                    mtime=datetime.now()
                )
                log.info(f'persisting new lyrics for {newrow["song"]} {newrow["artist"]} {mtime}')

                merged = session.merge(obj)
                session.add(merged)
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
        if s and ',' in s:
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
