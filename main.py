#!/usr/bin/env python3
import logging
import json
from logging.config import dictConfig

import requests
from bs4 import BeautifulSoup

dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '{asctime} {levelname} {process} [{filename}:{lineno}] - {message}',
            'style': '{',
        }
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
})

logger = logging.getLogger(__name__)


def scrape():
    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/134.0.0.0 Safari/537.36'
        )
    })

    res = session.get('https://play.limitlesstcg.com/decks?game=POCKET')
    soup = BeautifulSoup(res.text, 'html.parser')

    decks = []

    # first row is headers
    table_rows = soup.select('table.meta tr')[1:]
    for table_row in table_rows:
        share = round(float(table_row.get('data-share')), 4)

        try:
            winrate = round(float(table_row.get('data-winrate')), 4)
        except:
            winrate = 0.0

        cells = [cell.get_text(strip=True) for cell in table_row.select('td')]
        deck = cells[2]
        player_count = int(cells[3])

        score_parts = cells[5].split('-')
        wins = int(score_parts[0])
        losses = int(score_parts[1])
        ties = int(score_parts[2])

        decks.append({
            'deck': deck,
            'share': share,
            'winrate': winrate,
            'player_count': player_count,
            'wins': wins,
            'losses': losses,
            'ties': ties,
        })

    with open('decks.json', 'w') as f:
        json.dump(decks, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    scrape()
