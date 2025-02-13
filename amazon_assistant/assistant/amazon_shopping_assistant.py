import time
import logging
import json
import math
import tiktoken
import openai
import tiktoken

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from assistant.utils import num_tokens_from_messages

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class AmazonShoppingAssistant:
    def __init__(self):
        self.driver = self.setup_driver()
        self.top_products_cache = {}  # cache keyed by product index (as string)
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

        response = openai.Client(api_key=openai.api_key).chat.completions.create(**kwargs)
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
        try:
            elements = self.driver.find_elements(By.XPATH, "//div[contains(@data-cel-widget, 'search_result')]")
            return elements
        except Exception as e:
            logging.error("Error re-finding product elements: %s", e)
            return []

    def extract_product_url_from_element(self, element):
        """
        Attempts to extract the product URL from the search result element.
        This function looks for anchor tags and returns the first href that contains 'amazon.com'
        and ensures it uses 'www.amazon.com' (replacing unwanted subdomains).
        """
        try:
            anchors = element.find_elements(By.TAG_NAME, "a")
            for a in anchors:
                href = a.get_attribute("href")
                if href and "amazon.com" in href:
                    # Replace unwanted subdomain if present.
                    if "aax-us-iad.amazon.com" in href or "amazon.com/x/" in href:
                        return None
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
        for idx, elem in enumerate(elements, start=1):
            html = elem.get_attribute("outerHTML")
            product_details = self.parse_product_details_with_openai(html)
            product_details["index"] = idx  # store index for reference
            product_url = self.extract_product_url_from_element(elem)
            # If the URL is None, skip this product
            if product_url is None:
                continue
            product_details["product_url"] = product_url
            products.append(product_details)
            if len(products) >= 3:
                break
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
        Re-finds the product element by its index, clicks it to load its page,
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
