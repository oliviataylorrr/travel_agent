"""Settlement port. Simulation records ledger entries; no fake points currency.
Base Sepolia adapter is deliberately NOT enabled until actual swap/router code passes tests.
"""
from dataclasses import dataclass
from decimal import Decimal
import uuid
from .settings import PAYMENT_MODE
@dataclass(frozen=True)
class Receipt:
    status:str
    reference:str
    amount_usd:Decimal
    token_address:str

class SimulatedSettlement:
    def settle(self,amount_usd,recipient_token):
        return Receipt('simulated','SIM-'+uuid.uuid4().hex,Decimal(str(amount_usd)),recipient_token)

class BaseSepoliaSettlement:
    def settle(self,amount_usd,recipient_token):
        raise NotImplementedError('On-chain settlement adapter has not been implemented or audited. Keep PAYMENT_MODE=simulated.')

def settlement():
    if PAYMENT_MODE=='simulated':return SimulatedSettlement()
    if PAYMENT_MODE=='base_sepolia':return BaseSepoliaSettlement()
    raise ValueError('PAYMENT_MODE must be simulated or base_sepolia')
