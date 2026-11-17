"""Versioned deterministic adverse liquidity for the pinned native engine."""

from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.enums import BookType, OrderSide, OrderType
from nautilus_trader.model.objects import Quantity


class AdverseTickFill(FillModel):
    def __init__(self, ticks: int, clock=None, bar_closes: frozenset[int] = frozenset()) -> None:
        if ticks not in (1, 2, 4):
            raise ValueError("Supported stress distances are one, two or four ticks")
        super().__init__(prob_slippage=0, random_seed=0)
        self.ticks = ticks
        self.clock = clock
        self.bar_closes = bar_closes

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask) -> OrderBook:
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        distance = float(instrument.price_increment) * self.ticks
        bid, ask = float(best_bid), float(best_ask)
        if (
            order.order_type == OrderType.STOP_MARKET
            and self.clock is not None
            and self.clock() in self.bar_closes
        ):
            # Bar-close matching walks the native OHLC path through an existing stop.
            # Opening quote events have distinct timestamps and fill gaps at market.
            bid = ask = float(order.trigger_price)
        for identifier, side, price in (
            (1, OrderSide.BUY, bid - distance),
            (2, OrderSide.SELL, ask + distance),
        ):
            book.add(
                BookOrder(
                    side=side,
                    price=instrument.make_price(price),
                    size=Quantity(1_000_000, instrument.size_precision),
                    order_id=identifier,
                ),
                0,
                0,
            )
        return book
