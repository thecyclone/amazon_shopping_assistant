import time
import logging
import json
import math
import os
import requests
import openai
import tiktoken

from flask import Flask, render_template, request, redirect, url_for, Markup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# ----------------- Logging Setup -----------------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename="app.log",
                    filemode="w")

# ----------------- OpenAI Setup -----------------
openai.api_key = os.getenv("OPENAI_API_KEY_AA")
openai_client = openai.Client(api_key=openai.api_key)

def num_tokens_from_messages(messages, model="gpt-4o"):
    """
    Returns the number of tokens used by a list of messages.
    Adapted from OpenAI guidelines.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
    total_tokens = 0
    for message in messages:
        total_tokens += 4  # each message overhead
        for key, value in message.items():
            total_tokens += len(encoding.encode(value))
        total_tokens += 2
    return total_tokens

# ----------------- Assistant Class -----------------
class AmazonShoppingAssistant:
    def __init__(self):
        self.driver = self.setup_driver()
        self.top_products_cache = {}  # in-memory cache for product page HTML (if needed)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def setup_driver(self):
        chrome_options = Options()
        # Uncomment the next line to run Chrome in headless mode.
        # chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.93 Safari/537.36"
        )
        driver = webdriver.Chrome(options=chrome_options)
        driver.implicitly_wait(10)
        return driver

    def chat_completion_create(self, **kwargs):
        model = kwargs.get("model", "gpt-4o")
        messages = kwargs.get("messages", [])
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        input_tokens = num_tokens_from_messages(messages, model)
        self.total_input_tokens += input_tokens

        response = openai_client.chat.completions.create(**kwargs)
        output_text = response.choices[0].message.content
        output_tokens = len(encoding.encode(output_text))
        self.total_output_tokens += output_tokens
        return response

    def parse_query(self, query):
        return {"item": query, "max_price": None, "min_rating": None, "prime": False}

    def parse_query_with_openai(self, query):
        prompt = (
            "Extract the following search criteria from the user's shopping query:\n"
            "1. item: the product to search for (string).\n"
            "2. max_price: the maximum price (number) if specified, else null.\n"
            "3. min_rating: the minimum rating (number) if indicated (e.g., if the query says 'good reviews', assume 4.0), else null.\n"
            "4. prime: true if Prime shipping is required, false otherwise.\n"
            "Return the result as a valid JSON object with keys 'item', 'max_price', 'min_rating', and 'prime'.\n"
            f"User query: {query}"
        )
        try:
            response = self.chat_completion_create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Extract structured search criteria from shopping queries."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )
            answer = response.choices[0].message.content.strip()
            filters = json.loads(answer)
            logging.info("Parsed filters: %s", filters)
            return filters
        except Exception as e:
            logging.error("Error in parse_query_with_openai: %s", e)
            return self.parse_query(query)

    def parse_product_details_with_openai(self, html):
        prompt = (
            "Extract the following details from the provided HTML snippet of an Amazon product listing:\n"
            "1. title: product title (string).\n"
            "2. price: product price (number) if available, else null.\n"
            "3. rating: product rating (number) if available, else null.\n"
            "4. reviews: number of reviews (integer) if available, else null.\n"
            "5. prime: true if product has Prime shipping, false otherwise.\n"
            "6. url: product URL (string) if available, else null.\n"
            "Return a JSON object with keys 'title', 'price', 'rating', 'reviews', 'prime', and 'url'.\n"
            f"HTML snippet: {html}"
        )
        try:
            response = self.chat_completion_create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Extract product details from an Amazon listing HTML snippet."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            answer = response.choices[0].message.content.strip()
            product_details = json.loads(answer)
            return product_details
        except Exception as e:
            logging.error("Error in parse_product_details_with_openai: %s", e)
            return {"title": "No title", "price": None, "rating": None, "reviews": None, "prime": False, "url": None}

    def perform_search(self, filters):
        amazon_url = "https://www.amazon.com/"
        logging.info("Navigating to Amazon homepage")
        self.driver.get(amazon_url)
        time.sleep(2)
        try:
            search_box = self.driver.find_element(By.ID, "twotabsearchtextbox")
            search_box.clear()
            search_box.send_keys(filters["item"])
            search_box.submit()
            logging.info("Submitted query: %s", filters["item"])
            time.sleep(3)
        except Exception as e:
            logging.error("Error during search submission: %s", e)
            return
        if filters.get("max_price"):
            current_url = self.driver.current_url
            new_url = f"{current_url}&low-price=0&high-price={filters['max_price']}"
            logging.info("Applying price filter via URL: %s", new_url)
            self.driver.get(new_url)
            time.sleep(3)
        # Cache search page HTML.
        search_page_html = self.driver.page_source
        try:
            with open("search_page_cache.json", "w", encoding="utf-8") as f:
                json.dump({"html": search_page_html}, f)
            logging.info("Cached search page HTML.")
        except Exception as e:
            logging.error("Error caching search page HTML: %s", e)

    def get_product_elements(self):
        """
        Re-finds and returns the list of product elements on the current search page.
        """
        try:
            elements = self.driver.find_elements(By.XPATH, "//div[contains(@data-cel-widget, 'search_result')]")
            return elements
        except Exception as e:
            logging.error("Error re-finding product elements: %s", e)
            return []

    def extract_product_url_from_element(self, element):
        """
        Tries to extract the product URL from the search result element by locating an anchor tag.
        Adjust the XPath as needed based on the current Amazon DOM.
        """
        try:
            # Try finding any anchor tag with an href that contains "amazon.com"
            anchors = element.find_elements(By.TAG_NAME, "a")
            for a in anchors:
                href = a.get_attribute("href")
                if href and "amazon.com" in href:
                    return href
            return None
        except Exception as e:
            logging.error("Error extracting product URL from element: %s", e)
            return None

    def extract_products(self):
        products = []
        try:
            elements = self.get_product_elements()
            logging.info("Found %d product elements", len(elements))
        except Exception as e:
            logging.error("Error finding product elements: %s", e)
            return products
        for idx, elem in enumerate(elements[:10], start=1):
            html = elem.get_attribute("outerHTML")
            product_details = self.parse_product_details_with_openai(html)
            product_details["index"] = idx
            # Instead of clicking, extract URL from the element.
            product_url = self.extract_product_url_from_element(elem)
            product_details["product_url"] = product_url
            products.append(product_details)
        return products

    def get_user_priority_weights(self, priorities_str):
        if priorities_str.strip() == "":
            return "default", None
        else:
            try:
                weights = json.loads(priorities_str)
                logging.info("Custom weights: %s", weights)
                return "custom", weights
            except Exception as e:
                logging.error("Error parsing custom weights: %s", e)
                return "default", None

    def score_product(self, product, weights=None):
        rating = product.get("rating") or 0
        reviews = product.get("reviews") or 0
        price = product.get("price") if product.get("price") and product.get("price") > 0 else 1
        if weights is None:
            score = rating * math.log(reviews + 1) / price
        else:
            score = (weights.get("rating", 0) * rating) + (weights.get("reviews", 0) * math.log(reviews + 1)) - (weights.get("price", 0) * price)
        return score

    def decide_products(self, products, filters, weights=None):
        filtered_products = []
        for product in products:
            if filters.get("max_price") and product.get("price"):
                if product["price"] > filters["max_price"]:
                    continue
            if filters.get("min_rating") and product.get("rating"):
                if product["rating"] < filters["min_rating"]:
                    continue
            if filters.get("prime") and not product.get("prime"):
                continue
            filtered_products.append(product)
        scored_products = [(self.score_product(p, weights), p) for p in filtered_products]
        scored_products.sort(key=lambda x: x[0], reverse=True)
        top_products = [p for score, p in scored_products[:3]]
        return top_products

    def fetch_product_page_html_by_click(self, index):
        """
        Re-finds the product element by index, clicks it to load its page,
        retrieves the page HTML, then navigates back.
        """
        try:
            elements = self.get_product_elements()
            if index - 1 < len(elements):
                elem = elements[index - 1]
                elem.click()
                time.sleep(3)
                html = self.driver.page_source
                self.driver.back()
                time.sleep(3)
                return html
            else:
                logging.error("Product index %s out of range", index)
                return ""
        except Exception as e:
            logging.error("Error fetching product page HTML by index: %s", e)
            return ""

    def download_product_image(self, element, option_number):
        """
        Not used in this version.
        """
        return None

    def answer_question_with_details(self, question, product, cached_html):
        snippet_length = 1000
        snippet = cached_html[:snippet_length]
        context = (
            f"Product details:\n"
            f"  Title   : {product.get('title')}\n"
            f"  Price   : {product.get('price')}\n"
            f"  Rating  : {product.get('rating')}\n"
            f"  Reviews : {product.get('reviews')}\n"
            f"  Prime   : {product.get('prime')}\n"
            f"Product URL: {product.get('product_url')}\n"
            f"Cached HTML snippet (first {snippet_length} chars):\n{snippet}\n"
            f"Question: {question}\n"
            "Answer based on the product page information."
        )
        try:
            response = self.chat_completion_create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Answer follow-up questions using provided product page context."},
                    {"role": "user", "content": context}
                ],
                temperature=0.0,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
            return answer
        except Exception as e:
            logging.error("Error in answer_question_with_details: %s", e)
            return "Sorry, I couldn't fetch detailed information."

    def answer_followup_question(self, question, products):
        try:
            with open("search_page_cache.json", "r", encoding="utf-8") as f:
                search_cache = json.load(f)
            search_context = search_cache.get("html", "")[:1000]
        except Exception as e:
            logging.error("Error loading search page cache: %s", e)
            search_context = ""
        context = "Here are the product options:\n"
        for idx, product in enumerate(products, start=1):
            context += (
                f"Option {idx}:\n"
                f"  Title   : {product.get('title')}\n"
                f"  Price   : {product.get('price')}\n"
                f"  Rating  : {product.get('rating')}\n"
                f"  Reviews : {product.get('reviews')}\n"
                f"  Prime   : {product.get('prime')}\n"
                f"  URL     : {product.get('product_url')}\n\n"
            )
        full_context = f"Cached Search Page Context (first 1000 chars):\n{search_context}\n\n{context}"
        prompt = (
            f"Based on the above product options and cached search page context, answer the following follow-up question:\n{question}\n"
            "Provide a clear and concise answer."
        )
        try:
            response = self.chat_completion_create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Answer follow-up questions using provided search page context."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
            return answer
        except Exception as e:
            logging.error("Error in answer_followup_question: %s", e)
            return "Sorry, I could not process your question."

    def should_browse_question(self, question, product):
        context = (
            f"Product details: Title: {product.get('title')}, Price: {product.get('price')}, "
            f"Rating: {product.get('rating')}, Reviews: {product.get('reviews')}, Prime: {product.get('prime')}\n"
            f"Follow-up question: {question}\n"
            "Should browsing to the product page yield a better answer? Answer 'yes' or 'no'."
        )
        try:
            response = self.chat_completion_create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Decide if browsing to the product page will yield a better answer."},
                    {"role": "user", "content": context}
                ],
                temperature=0.0,
                max_tokens=50
            )
            answer = response.choices[0].message.content.strip().lower()
            return "yes" in answer
        except Exception as e:
            logging.error("Error in should_browse_question: %s", e)
            return False

    def process_followup(self, followup, products):
        option_number = None
        for i in range(1, 4):
            if f"option {i}" in followup.lower():
                option_number = i
                break
        if option_number is not None:
            product = None
            for p in products:
                if p.get("index") == option_number:
                    product = p
                    break
            if not product:
                logging.error("Could not find product with index %s", option_number)
                return "Sorry, I could not find the product."
            need_browse = self.should_browse_question(followup, product)
            if need_browse:
                cached_html = self.top_products_cache.get(str(option_number))
                if not cached_html:
                    cached_html = self.fetch_product_page_html_by_click(option_number)
                    self.top_products_cache[str(option_number)] = cached_html
                answer = self.answer_question_with_details(followup, product, cached_html)
            else:
                answer = self.answer_followup_question(followup, products)
        else:
            answer = self.answer_followup_question(followup, products)
        return answer

    def run(self, query, priorities=""):
        """
        Runs the full assistant process:
          1. Parse query.
          2. Perform search.
          3. Extract products.
          4. Determine priority weights from the provided string.
          5. Decide top 3 products.
          6. Cache top product pages.
        Returns the top 3 products.
        """
        filters = self.parse_query_with_openai(query)
        self.perform_search(filters)
        products = self.extract_products()
        type_, weights = self.get_user_priority_weights(priorities)
        if type_ == "default":
            weights = None
        top_products = self.decide_products(products, filters, weights)
        # Cache top 3 product pages (HTML) for later reference.
        top_products_cache = {}
        for product in top_products:
            index = product.get("index")
            html = self.fetch_product_page_html_by_click(index)
            top_products_cache[str(index)] = html
        try:
            with open("top_products_cache.json", "w", encoding="utf-8") as f:
                json.dump(top_products_cache, f)
            logging.info("Cached top products' pages.")
        except Exception as e:
            logging.error("Error caching top products' pages: %s", e)
        self.top_products_cache = top_products_cache
        return top_products

    def shutdown(self):
        self.driver.quit()

# ----------------- Flask Web App -----------------
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
    except Exception as e:
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
            base_rating = 1.0 if pref_rating else 0.5
            base_reviews = 1.0 if pref_reviews else 0.5
            base_price = 1.0 if pref_price else 0.5
            total = base_rating + base_reviews + base_price
            weights = {
                "rating": base_rating / total,
                "reviews": base_reviews / total,
                "price": base_price / total
            }
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
