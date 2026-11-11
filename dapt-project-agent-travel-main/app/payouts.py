"""Deliver Marketplace payout receipts over A2A and verify them before local storage."""
import asyncio
import logging
import re
import time

from .db import execute, read, transaction

log = logging.getLogger(__name__)


def enqueue_notice(connection, payment_id):
    connection.execute(
        'INSERT OR IGNORE INTO payout_outbox(payment_id) VALUES (?)', (payment_id,)
    )


async def store_receipt(notice):
    from .agent import market_api, profile

    payment_id = notice.get('payment_id')
    recipient = profile()['agent_id']
    if not isinstance(payment_id, str) or not re.fullmatch(r'PAY-[A-Za-z0-9-]{1,100}', payment_id):
        raise ValueError('Invalid payout ID')
    if notice.get('recipient') != recipient:
        raise ValueError('Payout notice is addressed to a different agent')

    payout = await market_api('GET', '/internal/payments/' + payment_id)
    if (
        payout['payment_id'] != payment_id
        or payout['recipient'] != recipient
        or payout['job_id'] != notice.get('job_id')
        or payout['kind'] != 'payout'
        or payout['status'] != 'simulated'
    ):
        raise ValueError('Notice does not match a recorded simulated payout')

    fields = ('payment_id', 'job_id', 'kind', 'amount_cents', 'recipient',
              'status', 'tx_reference', 'created_at')
    with transaction() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO payments
               (payment_id,job_id,kind,amount_cents,recipient,status,tx_reference,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            tuple(payout[field] for field in fields),
        )
    return {'stored': True, 'payment_id': payment_id}


async def deliver_once():
    from .agent import public_market, send_peer

    pending = read(
        """SELECT payments.*, payout_outbox.attempts
           FROM payout_outbox JOIN payments USING (payment_id)
           WHERE next_attempt_at<=? ORDER BY next_attempt_at,payment_id LIMIT 3""",
        (int(time.time()),),
    )
    if not pending:
        return
    registry = (await public_market('/api/registry'))['agents']
    urls = {agent['agent_id']: agent['url'] for agent in registry if agent['active']}
    for row in pending:
        payment_id = row['payment_id']
        try:
            notice = {key: row[key] for key in ('payment_id', 'job_id', 'recipient',
                                                'amount_cents', 'status', 'tx_reference')}
            notice.update(action='payment_received', currency='USD')
            ack = await asyncio.wait_for(send_peer(urls[row['recipient']], notice), timeout=20)
            if ack.get('stored') is not True or ack.get('payment_id') != payment_id:
                raise ValueError('Recipient did not acknowledge this payout')
            execute('DELETE FROM payout_outbox WHERE payment_id=?', (payment_id,))
        except Exception as exc:
            delay = min(300, 6 * 2 ** min(row['attempts'], 6))
            execute(
                'UPDATE payout_outbox SET attempts=attempts+1,next_attempt_at=? WHERE payment_id=?',
                (int(time.time()) + delay, payment_id),
            )
            log.warning('Payout notice %s deferred (%s)', payment_id, type(exc).__name__)


async def deliver_loop(stop):
    while not stop.is_set():
        try:
            await deliver_once()
        except Exception:
            log.exception('Payout delivery loop failed')
        try:
            await asyncio.wait_for(stop.wait(), timeout=6)
        except asyncio.TimeoutError:
            pass
