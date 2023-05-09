#!/usr/bin/env python3

import asyncio
import sys
import time
import aiohttp

async def post_annotation(tag, text):
    anno = {
        'tags': [tag],
        'time': int(time.time()*1000),
        'text': text
    }
    async def do_post():
        async with aiohttp.ClientSession() as session:
            async with session.post('http://localhost:3000/api/annotations',
                                    json=anno) as response:
                pass
    await do_post()

asyncio.run(post_annotation('script', sys.argv[1]))
