import json
from flask import Flask, render_template, request, redirect, url_for, Markup
from assistant.amazon_shopping_assistant import AmazonShoppingAssistant
from assistant.utils import normalize_weights

app = Flask(__name__)
app.secret_key = "a_secret_key_for_session"

# Global state
assistant = AmazonShoppingAssistant()
TOP_PRODUCTS = None
CONVERSATION_HISTORY = []  # List of dicts: {"sender": ..., "message": ...}

def get_logs():
    try:
        with open("app.log", "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "No logs available."

@app.route("/", methods=["GET", "POST"])
def index():
    global TOP_PRODUCTS, CONVERSATION_HISTORY
    if request.method == "POST":
        query = request.form.get("query")
        # Read checkbox values for priorities.
        pref_rating = request.form.get("pref_rating")
        pref_reviews = request.form.get("pref_reviews")
        pref_price = request.form.get("pref_price")
        if not (pref_rating or pref_reviews or pref_price):
            priorities = ""
        else:
            weights = normalize_weights(pref_rating, pref_reviews, pref_price)
            priorities = json.dumps(weights)
        top_products = assistant.run(query, priorities)
        TOP_PRODUCTS = top_products
        CONVERSATION_HISTORY = []  # reset conversation history
        return redirect(url_for("chat"))
    return render_template("index.html")

@app.route("/chat", methods=["GET", "POST"])
def chat():
    global CONVERSATION_HISTORY, TOP_PRODUCTS
    if request.method == "POST":
        message = request.form.get("message")
        CONVERSATION_HISTORY.append({"sender": "User", "message": message})
        answer = assistant.process_followup(message, TOP_PRODUCTS)
        CONVERSATION_HISTORY.append({"sender": "Agent", "message": answer})
        return redirect(url_for("chat"))
    return render_template("chat.html", top_products=TOP_PRODUCTS, conversation_history=CONVERSATION_HISTORY, logs=Markup(get_logs()))

@app.route("/summary")
def summary():
    total_input = assistant.total_input_tokens
    total_output = assistant.total_output_tokens
    cost_input = (total_input / 1_000_000) * 2.5
    cost_output = (total_output / 1_000_000) * 10
    total_cost = cost_input + cost_output
    return f"<h2>Token Usage Summary</h2><p>Total Input Tokens: {total_input}</p><p>Total Output Tokens: {total_output}</p><p>Estimated Cost: ${total_cost:.4f}</p>"

if __name__ == "__main__":
    port = 5001
    print(f"Starting the Flask app on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
