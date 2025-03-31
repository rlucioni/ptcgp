#!/usr/bin/env python3
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.config import dictConfig

import requests
from bs4 import BeautifulSoup
from urllib3.util import Retry

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


ORIGIN = 'https://play.limitlesstcg.com'
MIN_GAMES = 100
MAX_DECKLISTS = 3


def md5(str_value):
    return hashlib.md5(str_value.encode()).hexdigest()


class Timer:
    def __init__(self):
        self.t0 = time.time()

    def done(self):
        self.latency = time.time() - self.t0


class ProgressMeter:
    def __init__(self, total, msg='{done}/{total} ({percent}%) done', mod=10):
        self.total = total
        self.done = 0
        self.msg = msg
        self.mod = mod

    def increment(self):
        self.done += 1

        if self.done % self.mod == 0:
            percent = round((self.done / self.total) * 100)
            logger.info(self.msg.format(done=self.done, total=self.total, percent=percent))


class Spider:
    def __init__(self, pool_maxsize=16):
        self.pool_maxsize = pool_maxsize
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/134.0.0.0 Safari/537.36'
            )
        })

        retries = Retry(
            total=3,
            backoff_factor=0.1,
            status_forcelist=[502, 503, 504],
            allowed_methods={'GET', 'POST'},
            raise_on_status=False,
        )

        self.session.mount(
            'https://',
            requests.adapters.HTTPAdapter(pool_maxsize=self.pool_maxsize, max_retries=retries)
        )

    def crawl(self):
        deck_res = self.session.get(f'{ORIGIN}/decks?game=POCKET')
        deck_soup = BeautifulSoup(deck_res.text, 'html.parser')

        decks = []
        # first row is headers
        deck_table_rows = deck_soup.select('table.meta tr')[1:]
        for deck_table_row in deck_table_rows:
            share = round(float(deck_table_row.get('data-share')), 4)

            try:
                winrate = round(float(deck_table_row.get('data-winrate')), 4)
            except:
                winrate = 0.0

            cells = deck_table_row.select('td')
            cell_texts = [cell.get_text(strip=True) for cell in cells]

            deck_name = cell_texts[2]
            deck_path = cells[2].select_one('a').get('href')
            player_count = int(cell_texts[3])

            record_parts = cell_texts[5].split('-')
            wins = int(record_parts[0])
            losses = int(record_parts[1])
            ties = int(record_parts[2])
            games = wins + losses + ties

            decks.append({
                'deck_name': deck_name,
                'url': f'{ORIGIN}{deck_path}',
                'share': share,
                'player_count': player_count,
                'games': games,
                'wins': wins,
                'losses': losses,
                'ties': ties,
                'winrate': winrate,
                'best_finishes': [],
            })

        decks.sort(key=lambda deck: (deck['player_count'], deck['games']), reverse=True)
        filtered_decks = [deck for deck in decks if deck['games'] >= MIN_GAMES]

        if filtered_decks:
            decks = filtered_decks
        else:
            decks = decks[:10]

        finishes_timer = Timer()
        finishes_progress = ProgressMeter(len(decks), msg='{done}/{total} ({percent}%) decks done')
        with ThreadPoolExecutor(max_workers=self.pool_maxsize) as executor:
            futures = {}
            for deck in decks:
                future = executor.submit(self.attach_finishes, deck)
                futures[future] = deck['url']

            for future in as_completed(futures):
                finishes_progress.increment()
                deck_url = futures[future]

                try:
                    _ = future.result()
                except:
                    logger.exception(f'failed to attach finishes for {deck_url}')
                    continue

        finishes_timer.done()
        logger.info(f'done attaching finishes in {round(finishes_timer.latency, 2)}s')

        cards_timer = Timer()
        cards_progress = ProgressMeter(
            sum([len(deck['best_finishes']) for deck in decks]),
            msg='{done}/{total} ({percent}%) finishes done'
        )
        with ThreadPoolExecutor(max_workers=self.pool_maxsize) as executor:
            futures = {}
            for deck in decks:
                for finish in deck['best_finishes']:
                    future = executor.submit(self.attach_cards, finish)
                    futures[future] = finish['decklist_url']

            for future in as_completed(futures):
                cards_progress.increment()
                decklist_url = futures[future]

                try:
                    _ = future.result()
                except:
                    logger.exception(f'failed to attach cards for {decklist_url}')
                    continue

        cards_timer.done()
        logger.info(f'done attaching cards in {round(cards_timer.latency, 2)}s')

        for deck in decks:
            # don't want this in the final output, easy to strip it here
            deck.pop('url')

            decklists = {}
            finishes = deck.pop('best_finishes')

            for finish in finishes:
                card_hash = md5(''.join(finish['cards']))
                decklist = decklists.get(card_hash)

                if decklist:
                    decklist['player_count'] += 1
                    decklist['games'] += (finish['wins'] + finish['losses'] + finish['ties'])
                    decklist['wins'] += finish['wins']
                    decklist['losses'] += finish['losses']
                    decklist['ties'] += finish['ties']
                else:
                    decklists[card_hash] = {
                        'cards': finish['cards'],
                        'player_count': 1,
                        'games': finish['wins'] + finish['losses'] + finish['ties'],
                        'wins': finish['wins'],
                        'losses': finish['losses'],
                        'ties': finish['ties'],
                    }

            for decklist in decklists.values():
                decklist['winrate'] = round(decklist['wins'] / decklist['games'], 4)

            sorted_decklists = sorted(
                decklists.values(),
                key=lambda decklist: (decklist['player_count'], decklist['games']),
                reverse=True
            )
            filtered_decklists = [decklist for decklist in sorted_decklists if decklist['games'] >= MIN_GAMES]

            if filtered_decklists:
                deck['decklists'] = filtered_decklists[:MAX_DECKLISTS]
            else:
                deck['decklists'] = sorted_decklists[0]

        with open('decks.json', 'w') as f:
            json.dump(decks, f, indent=2, ensure_ascii=False)

    def attach_finishes(self, deck):
        finishes_res = self.session.get(deck['url'])
        finishes_soup = BeautifulSoup(finishes_res.text, 'html.parser')

        # first row is headers
        finishes_table_rows = finishes_soup.select('table.striped tr')[1:]
        for finishes_table_row in finishes_table_rows:
            cells = finishes_table_row.select('td')
            cell_texts = [cell.get_text(strip=True) for cell in cells]

            record_parts = cell_texts[4].split('-')
            wins = int(record_parts[0])
            losses = int(record_parts[1])
            ties = int(record_parts[2])

            decklist_path = cells[5].select_one('a').get('href')

            deck['best_finishes'].append({
                'decklist_url': f'{ORIGIN}{decklist_path}',
                'wins': wins,
                'losses': losses,
                'ties': ties,
            })

    def attach_cards(self, finish):
        decklist_res = self.session.get(finish['decklist_url'])
        decklist_soup = BeautifulSoup(decklist_res.text, 'html.parser')

        finish['cards'] = sorted(
            [card.get_text(strip=True) for card in decklist_soup.select('.cards p')],
            reverse=True
        )


if __name__ == '__main__':
    spider = Spider()
    spider.crawl()
