from src.gta_rewind_provider import RewindClient

class Resp:
    def __init__(self, body): self.body=body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self,*a): pass

def test_rewind_explicit_success():
    c=RewindClient(token="x", opener=lambda *a,**k: Resp(b'{"result":{"value":{"err":null},"slot":7}}'))
    e=c.simulate("tx", anchor={"slot":7}, test_conditions="historical buy sell")
    assert e.status=="sellable" and e.observation_slot==7

def test_missing_token_is_provider_error():
    assert RewindClient(token="").simulate("tx", anchor={}, test_conditions="x").status=="provider_error"
