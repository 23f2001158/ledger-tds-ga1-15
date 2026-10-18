
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

app = Flask(__name__)

DATA_FILE = "ledger.jsonl"
TIMEZONE = ZoneInfo("Asia/Kolkata")

USD_PER_UNIT = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.04"),
    "INR": Decimal("0.0113"),
}


def parse_date(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(TIMEZONE)


def load_orders():
    latest = {}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            order = json.loads(line)
            order_id = order["id"]

            if order_id not in latest:
                latest[order_id] = order
            else:
                old_time = parse_date(latest[order_id]["updated_at"])
                new_time = parse_date(order["updated_at"])

                if new_time > old_time:
                    latest[order_id] = order

    orders = list(latest.values())

    for order in orders:
        order["_date"] = parse_date(order["created_at"])
        order["_usd"] = (
            Decimal(str(order["amount"]))
            * USD_PER_UNIT[order["currency"]]
        )

    return orders


ORDERS = load_orders()


def money(value):
    value = Decimal(str(value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(value)


def answer_question(question):
    q = question.lower().strip()

    # Date filters
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else None

    months = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }

    month = None
    for name, number in months.items():
        if re.search(r"\b" + name + r"\b", q):
            month = number
            break

    # Region filter
    regions = ["North", "South", "East", "West", "Central"]
    region = next(
        (r for r in regions if re.search(r"\b" + r.lower() + r"\b", q)),
        None,
    )

    # Product filter: match the exact product names from the ledger
    products = sorted(
        {o["product"] for o in ORDERS},
        key=len,
        reverse=True,
    )
    product = next(
        (p for p in products if p.lower() in q),
        None,
    )

    # Customer filter
    customer_match = re.search(r"\bC\d+\b", question, re.I)
    customer = customer_match.group(0).upper() if customer_match else None

    # Status and intent
    asks_refunds = any(
        word in q for word in ["refund", "refunded", "refunds"]
    )
    asks_void = any(word in q for word in ["void", "voided", "cancelled", "canceled"])
    asks_count = any(
        word in q
        for word in ["how many", "number of", "count", "quantity of orders"]
    )
    asks_average = any(word in q for word in ["average", "avg", "mean"])
    asks_top = any(
        word in q for word in ["most", "highest", "top", "best-selling", "best selling"]
    )
    asks_customers = "customer" in q and asks_top
    asks_products = "product" in q and asks_top

    # Apply filters
    filtered = []

    for order in ORDERS:
        dt = order["_date"]

        if year is not None and dt.year != year:
            continue
        if month is not None and dt.month != month:
            continue
        if region and order["region"].lower() != region.lower():
            continue
        if product and order["product"].lower() != product.lower():
            continue
        if customer and order["customer"].upper() != customer:
            continue

        filtered.append(order)

    if asks_refunds:
        filtered = [o for o in filtered if o["status"] == "refunded"]
    elif asks_void:
        filtered = [o for o in filtered if o["status"] == "void"]
    else:
        # Revenue is defined by the assignment as paid orders only.
        if any(w in q for w in ["revenue", "sales", "earned", "income"]):
            filtered = [o for o in filtered if o["status"] == "paid"]

    if not filtered:
        return 0

    # Ranking questions
    if asks_products:
        totals = defaultdict(Decimal)
        for o in filtered:
            if o["status"] == "paid":
                totals[o["product"]] += o["_usd"]
        if not totals:
            return None
        return max(totals, key=totals.get)

    if asks_customers:
        totals = defaultdict(Decimal)
        for o in filtered:
            if o["status"] == "paid":
                totals[o["customer"]] += o["_usd"]
        if not totals:
            return None
        return max(totals, key=totals.get)

    # Count questions
    if asks_count:
        return len(filtered)

    # Refund amount questions
    if asks_refunds:
        total = sum((o["_usd"] for o in filtered), Decimal("0"))
        if asks_average:
            total /= len(filtered)
        return money(total)

    # Other monetary questions default to USD
    total = sum((o["_usd"] for o in filtered), Decimal("0"))

    if asks_average:
        total /= len(filtered)

    if asks_top:
        # Top product/customer is handled above.
        # If the question asks for the highest individual order:
        highest = max(filtered, key=lambda o: o["_usd"])
        return money(highest["_usd"])

    return money(total)


@app.route("/answer", methods=["POST"])
def answer():
    data = request.get_json(silent=True) or {}
    question = data.get("question")

    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "Provide a non-empty question"}), 400

    try:
        result = answer_question(question)
        return jsonify({"answer": result})
    except Exception:
        app.logger.exception("Question processing failed")
        return jsonify({"error": "Could not process question"}), 500


@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "service": "Acme Appliances Ledger Agent",
        "status": "ready",
        "orders_loaded": len(ORDERS),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
