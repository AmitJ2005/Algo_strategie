import ssl, sys
import upstox_client
import asyncio, json
import websockets
from google.protobuf.json_format import MessageToDict
from threading import Thread, Lock
import MarketDataFeed_pb2 as pb
from time import sleep
import requests as rq
import pandas as pd
import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()
dotenv_path = '.env'

access_token = os.getenv("ACCESS_TOKEN")

# Load the all instruments 
df = pd.read_csv("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz")

# get banknifty option chain data from all instruments data
def filter_df(df, lot_size):
    df = df[(df['exchange'] == 'NSE_FO') & (df['instrument_type'] == 'OPTIDX') & (df['lot_size'] == lot_size)]
    df = df[df['expiry'] == min(df['expiry'].unique())]
    return df

# for last price data from option chain
def get_quotes(instrument):
    url = 'https://api-v2.upstox.com/market-quote/ltp'
    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Authorization': f'Bearer {access_token}'
    }
    params = {'symbol': instrument, 'interval': '1d'}
    try:
        res = rq.get(url, headers=headers, params=params)
        res.raise_for_status()
        return res.json()
    except rq.exceptions.RequestException as e:
        print(f"Error fetching quotes: {e}")
        return None

# Find the near ATM option with trade symbol
def find_option(near_val, optionChain):
    call_symbol = {}
    put_symbol = {}
    trade_symbol = {}
    for i in range(5):
        try:
            ltp_data = get_quotes(optionChain)['data']
        except Exception as e:
            print(f"Error getting quotes: {e}")
            sleep(0.5)
            continue
        for k, v in ltp_data.items():
            # Call Symbol
            if float(v['last_price']) <= near_val and k[-2:] == 'CE':
                call_symbol.update({v['instrument_token']: float(v['last_price'])})
            # Put Symbol
            if float(v['last_price']) <= near_val and k[-2:] == 'PE':
                put_symbol.update({v['instrument_token']: float(v['last_price'])})
        if call_symbol and put_symbol:
            ce_val = min(list(call_symbol.values()), key=lambda x: abs(x - near_val))
            pe_val = min(list(put_symbol.values()), key=lambda x: abs(x - near_val))
            for a, b in call_symbol.items():
                if b == ce_val:
                    trade_symbol.update({a: b})
            for c, d in put_symbol.items():
                if d == pe_val:
                    trade_symbol.update({c: d})
        if trade_symbol:
            return trade_symbol
        else:
            sleep(1)
    return 'Symbol Not found'

# Place order and get order history
def place_order(symbol, qty, direction):
    url = "https://api.upstox.com/v2/order/place"
    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    payload = {
        "quantity": qty,
        "product": "I",
        "validity": "DAY",
        "price": 0,
        "tag": "string",
        "instrument_token": symbol,
        "order_type": "MARKET",
        "transaction_type": direction,
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False
    }
    data = json.dumps(payload)
    try:
        response = rq.post(url, headers=headers, data=data)
        response.raise_for_status()
        return response.json()['data']['order_id']
    except rq.exceptions.RequestException as e:
        print(f"Error placing order: {e}")
        return None

def get_order_history(oid):
    url = "https://api.upstox.com/v2/order/details"
    payload = {'order_id': oid}
    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    try:
        response = rq.get(url, headers=headers, params=payload)
        response.raise_for_status()
        return response.json()['data']
    except rq.exceptions.RequestException as e:
        print(f"Error fetching order history: {e}")
        return None

# websocket part, 4 functions
def get_market_data_feed_authorize(api_version, configuration):
    """Get authorization for market data feed."""
    api_instance = upstox_client.WebsocketApi(upstox_client.ApiClient(configuration))
    api_response = api_instance.get_market_data_feed_authorize(api_version)
    return api_response

def decode_protobuf(buffer):
    """Decode protobuf message."""
    feed_response = pb.FeedResponse()
    feed_response.ParseFromString(buffer)
    return feed_response

async def fetch_market_data():
    global data_dict
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    configuration = upstox_client.Configuration()
    api_version = '2.0'
    configuration.access_token = access_token
    response = get_market_data_feed_authorize(api_version, configuration)
    async with websockets.connect(response.data.authorized_redirect_uri, ssl=ssl_context) as websocket:
        print('Connection established')
        await asyncio.sleep(1)  # Wait for 1 second
        data = {
            "guid": "someguid",
            "method": "sub",
            "data": {
                "mode": "ltpc",
                "instrumentKeys": symbol
            }
        }
        binary_data = json.dumps(data).encode('utf-8')
        await websocket.send(binary_data)
        while True:
            message = await websocket.recv()
            decoded_data = decode_protobuf(message)
            with data_lock:
                data_dict = MessageToDict(decoded_data)

def run_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(fetch_market_data())



# Input Parameters *****
buy_percent = 10
qty = 15
premium_range = 400
sl = 20 #point
tsl = 10 #point
max_trades = 2 
# **************************

trade_count = 0  # Initialize trade count

BNDF = filter_df(df, 15)
optionChain = list(BNDF['instrument_key'])

trade_symbol = find_option(premium_range, optionChain)
symbol = list(trade_symbol)

data_dict = {}
data_lock = Lock()

websocket_thread = Thread(target=run_websocket)
websocket_thread.start()
sleep(5)
trade = None

while True:
    sleep(1)
    with data_lock:
        for name in symbol:
            if name in data_dict.get('feeds', {}):
                ltp = data_dict['feeds'][name]['ltpc']['ltp']
                print(name, ltp)

                if ltp >= (trade_symbol[name] * buy_percent) / 100 and trade is None and trade_count < max_trades:
                    try:
                        oid = place_order(name, qty, 'BUY')
                        if oid:
                            orderHistory = get_order_history(oid)
                            if orderHistory and orderHistory['status'] == 'complete':
                                avgPrc = orderHistory['average_price']
                                sl = avgPrc - sl
                                tsl = avgPrc + tsl
                                option = name
                                trade = 1
                                trade_count += 1  # Increment trade count
                                print(f"Buy Trade In {name} Price : {avgPrc}")
                            else:
                                print(f"Order not completed. Status: {orderHistory['status'] if orderHistory else 'Unknown'}, Order ID: {oid}")
                                continue
                        else:
                            print("Failed to place order.")
                            continue
                    except Exception as e:
                        print(f"Error placing order or fetching order history: {e}")
                        continue

                if trade == 1 and ltp >= tsl and option == name:
                    tsl += 2
                    sl += 2
                    print(f"Buy SL trailed {option} SL: {sl}")

                if trade == 1 and ltp <= sl and option == name:
                    try:
                        oid = place_order(option, qty, 'SELL')
                        if oid:
                            print(f"Buy {option} Exit {ltp}")
                            trade = None  # Reset trade
                        else:
                            print("Failed to place sell order.")
                    except Exception as e:
                        print(f"Error placing sell order: {e}")
                        continue
            else:
                print(f"Warning: {name} not found in data feed.")