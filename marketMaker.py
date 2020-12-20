import keys
import time
import math
import sys
from binance.client import Client
from time import sleep
import pandas as pd

# counts number of iterations to manage data collection and storage
iter_count = 0

buy_order = {}
sell_order = {}
trade_data = {
    'time': [],
    'symbol': [],
    'side': [],
    'executedQty': [],
    'price': []
}

# Connects to Binance server
client = Client(keys.API_KEY, keys.SECRET_KEY, tld='us')

# Prompts the user which currency pair they would like to trade
base_currency = input('What is your base currency?\n')  # COMP
quote_currency = input('What is your quote currency?\n')   # USDT
market = base_currency + quote_currency
x = {'symbol': market}

# Prompts user how long they would like the program to run
endTime = int(time.time() + float(input('How long would you like the program to run? (seconds)\n')))
startTime = time.time()

# Calculates relevant info necessary for order placement
ticker_info = client.get_symbol_info(market)
min_order_size = float(ticker_info['filters'][3]['minNotional']) + 0.5
precision = 2


def get_percent_completed() -> float:
    """
    sees how much time has elapsed as a percent of total run time.
    used to get back to risk-neutral once the program completes running.
    """
    time_elapsed = time.time() - startTime
    total_time = endTime - startTime
    return time_elapsed / total_time


def truncate(number, digits) -> float:
    """truncates to certain number of decimals"""
    stepper = 10.0 ** digits
    return math.trunc(stepper * number) / stepper


def get_total_value() -> float:
    """finds the total value in USD of the two assets in the portfolio"""
    base_amt = float(client.get_asset_balance(asset=base_currency)['free'])
    base_val_usd = base_amt * float(client.get_orderbook_ticker(**x)['bidPrice'])
    quote_val_usd = float(client.get_asset_balance(asset=quote_currency)['free'])
    return quote_val_usd + base_val_usd


max_order_size = (0.2*get_total_value()) / float(client.get_orderbook_ticker(**x)['bidPrice'])


def get_asset_ratio() -> float:
    """finds proportion of portfolio currently invested in the base currency"""
    base_amt = float(client.get_asset_balance(asset=base_currency)['free'])
    base_val_usd = base_amt * float(client.get_orderbook_ticker(**x)['bidPrice'])
    return (base_val_usd / get_total_value()) - 0.5


def get_market_spread() -> float:
    """
    finds the spread of the market with respect to price.
    adjusts spreads to tighten as program gets closer to closing
    """
    order_book = client.get_order_book(symbol=market)
    best_bid = float(order_book['bids'][0][0])
    best_ask = float(order_book['asks'][0][0])
    market_width = 0.25*(best_ask - best_bid)
    base_spread = market_width / float(client.get_orderbook_ticker(**x)['bidPrice'])
    return 0.001 + base_spread*(1-get_percent_completed())


def get_market_price() -> float:
    """
    returns the average of the best bid and best ask prices
    """
    order_book = client.get_order_book(symbol=market)
    best_bid = float(order_book['bids'][0][0])
    best_ask = float(order_book['asks'][0][0])
    return (best_bid + best_ask) / 2


def get_bid_size() -> float:
    """
    determines the size of the bid order to be placed, based on a dynamic strategy.
    bid size is maximum if the value of quote currency is higher than 50% as a
    percentage of total portfolio value. Exponential decay as value of quote currency
    in portfolio decreases from 50% to 0%.
    """
    if 2*float(client.get_asset_balance(asset=quote_currency)['free']) > get_total_value():
        return max_order_size
    else:
        return max_order_size * math.exp(-5*get_asset_ratio())


def get_ask_size() -> float:
    """
    determines the size of the ask order to be placed, based on a dynamic strategy.
    offer size is maximum if the value of quote currency is less than 50% as a
    percentage of total portfolio value. Exponential decay as value of quote currency
    in portfolio increases from 50% to 100%.
    """
    if 2*float(client.get_asset_balance(asset=quote_currency)['free']) < get_total_value():
        return max_order_size
    else:
        return max_order_size * math.exp(5*get_asset_ratio())


def get_bid_price():
    """
    gets the desired price to bid. Made as a function of the bid-ask spread of the market
    and the time left until the program stops running
    """
    market_price = get_market_price()
    spread = get_market_spread()
    return market_price * (1-spread)


def get_ask_price():
    """
    gets the desired price to ask. Made as a function of the bid-ask spread of the market
    and the time left until the program stops running
    """
    market_price = get_market_price()
    spread = get_market_spread()
    return market_price * (1+spread)


def place_bid() -> dict:
    """
    places the bid offer
    """
    global buy_order
    size = truncate(get_bid_size(), precision)
    price = truncate(get_bid_price(), precision)
    bid = size * price
    if bid > min_order_size:
        buy_order = client.create_order(
            symbol=market,
            side=Client.SIDE_BUY,
            type='LIMIT_MAKER',
            quantity=size,
            price=price,
            timestamp=time.time(),
            newOrderRespType='RESULT'
        )
        print("Buy order placed for ", size, " ", base_currency, "at a price of ", price)
        return buy_order
    else:
        print('Buy order too small')
        return {}


def place_ask() -> dict:
    """
    places the ask offer
    """
    global sell_order
    size = truncate(get_ask_size(), precision)
    price = truncate(get_ask_price(), precision)
    ask = size * price
    if ask > min_order_size:
        sell_order = client.create_order(
            symbol=market,
            side=Client.SIDE_SELL,
            type='LIMIT_MAKER',
            quantity=size,
            price=price,
            timestamp=time.time(),
            newOrderRespType='RESULT'
        )
        print("Sell order placed for ", size, " ", base_currency, "at a price of ", price)
        return sell_order
    else:
        print('Sell order too small')
        return {}


def get_filled_orders():
    """
    since the orders are deleted by the API once they are filled, this serves to retrieve the filled
    """
    all_orders = client.get_all_orders(**x)
    filled_orders = []
    for order in all_orders:
        if order['status'] == 'FILLED':
            filled_orders.append(order)
    return filled_orders


def cancel_all_orders():
    orders = client.get_open_orders(symbol=market)
    for order in orders:
        client.cancel_order(
            symbol=market,
            orderId=order['orderId']
        )


def record_orders():
    """
    filters out all orders that did not execute and stores them in the data frame
    """
    all_orders = client.get_all_orders(**x)
    for order in all_orders:
        if float(order['executedQty']) != 0.00:
            trade_data['time'].append(order['time'])
            trade_data['symbol'].append(order['symbol'])
            trade_data['side'].append(order['side'])
            trade_data['executedQty'].append(order['executedQty'])
            trade_data['price'].append(order['price'])


def main():
    """manages the order book"""
    global iter_count
    # Initializes by closing any open orders on the book
    cancel_all_orders()

    while time.time() < endTime:
        if iter_count < 200:
            iter_count += 1
        else:
            record_orders()
            iter_count = 0
        order_count = len(client.get_open_orders(**x))
        # if both have executed
        if order_count == 0:
            place_ask()
            place_bid()
            sleep(5)
        # if only one has executed
        elif order_count == 1:
            sleep(15)
            # if one executes and the other does not within 15 seconds, reset orders
            if order_count == 1:
                cancel_all_orders()
                place_ask()
                place_bid()
                sleep(15)
        elif order_count == 2:
            if float(client.get_server_time()['serverTime']) - float(buy_order['transactTime']) > 15:
                cancel_all_orders()
                place_ask()
                place_bid()
                sleep(5)
            else:
                sleep(5)
    cancel_all_orders()
    df = pd.DataFrame(trade_data).sort_values(by='time').reset_index(drop=True)
    df.to_csv('tradeData.csv', index=False)
    print('process complete')
    sys.exit()


if __name__ == '__main__':
    main()
