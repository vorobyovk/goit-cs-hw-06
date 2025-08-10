import json
import logging
import mimetypes
import os
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from multiprocessing import Process
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from pymongo import MongoClient

import config  # Import configuration from config.py

# Use imported config variables
HTTP_HOST = config.HTTP_HOST
HTTP_PORT = config.HTTP_PORT
SOCKET_HOST = config.SOCKET_HOST
SOCKET_PORT = config.SOCKET_PORT
MONGO_URI = config.MONGO_URI
DB_NAME = config.DB_NAME
COLLECTION_NAME = config.COLLECTION_NAME

# Логування
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Додаємо базовий шлях до шаблонів
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


# HTTP Handler
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP-обробник для обробки веб-запитів.

    Підтримує:
    - GET запити для відображення сторінок
    - POST запити для обробки повідомлень
    - Відправку статичних файлів
    """

    def do_GET(self):
        """Обробляє GET-запити від клієнта."""
        parsed_path = urlparse(self.path)
        route = parsed_path.path
        match route:
            case "/" | "/index.html":
                self.send_html_file("index.html")
            case "/message.html":
                self.send_html_file("message.html")
            case "/error.html":
                self.send_html_file("error.html")
            case _ if route.startswith("/static/"):
                self.send_static_file(route[1:])
            case _:
                self.send_error_page()

    def do_POST(self):
        """Обробляє POST-запити від клієнта."""
        if self.path == "/message":
            content_length = int(self.headers.get("Content-Length"))
            body = self.rfile.read(content_length)
            params = parse_qs(body.decode())
            message = {
                "username": params.get("username", [""])[0],
                "message": params.get("message", [""])[0],
            }
            self.send_to_socket(message)
            self.redirect_to_home()
        else:
            self.send_error_page()

    def send_to_socket(self, message):
        # Валідація вхідних даних
        if not message.get("username") or not message.get("message"):
            logging.error("Відсутні обов'язкові поля 'username' або 'message'")
            self.send_error_page()
            return
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.connect((SOCKET_HOST, SOCKET_PORT))
                client_socket.sendall(json.dumps(message).encode("utf-8"))
        except Exception as e:
            logging.error(f"Помилка при відправці до сокет-сервера: {e}")

    def redirect_to_home(self):
        """Перенаправляє клієнта на головну сторінку."""
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    def send_html_file(self, filename, status_code=200):
        """Надсилає HTML-файл клієнту."""
        try:
            filepath = os.path.join(TEMPLATE_DIR, filename)
            with open(filepath, "rb") as file:
                self.send_response(status_code)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(file.read())
        except FileNotFoundError:
            logging.error(f"Файл не знайдено: {filename}")
            self.send_error_page()

    def send_static_file(self, filepath):
        """Надсилає статичний файл клієнту."""
        try:
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            full_path = os.path.join(static_dir, os.path.basename(filepath))
            mimetype, _ = mimetypes.guess_type(full_path)
            with open(full_path, "rb") as file:
                self.send_response(200)
                self.send_header("Content-type", mimetype or "application/octet-stream")
                self.end_headers()
                self.wfile.write(file.read())
        except FileNotFoundError:
            logging.error(f"Статичний файл не знайдено: {filepath}")
            self.send_error_page()

    def send_error_page(self):
        """Надсилає сторінку помилки клієнту."""
        self.send_html_file("error.html", status_code=404)


# Threading HTTP Server
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Багатопотоковий HTTP-сервер для паралельної обробки запитів."""

    daemon_threads = True  # Додаємо для автоматичного завершення потоків


# Socket Server
def run_socket_server():
    """
    Запускає сокет-сервер для обробки повідомлень.

    Створює підключення до MongoDB та очікує на вхідні повідомлення.
    Кожне повідомлення обробляється в окремому потоці.
    """
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind((SOCKET_HOST, SOCKET_PORT))
            server_socket.listen(5)
            logging.info(f"Сокет-сервер запущено на {SOCKET_HOST}:{SOCKET_PORT}")

            while True:
                conn, addr = server_socket.accept()
                thread = threading.Thread(
                    target=handle_socket_connection, args=(conn, addr, collection)
                )
                thread.start()
    except Exception as e:
        logging.error(f"Помилка запуску сокет-сервера: {e}")


# Handle socket connections
def handle_socket_connection(conn, addr, collection):
    """
    Обробляє вхідне сокет-з'єднання.

    Параметри:
        conn: Об'єкт з'єднання сокета
        addr: Адреса клієнта
        collection: Колекція MongoDB для збереження повідомлень
    """
    try:
        data = conn.recv(1024)
        if data:
            message = json.loads(data.decode("utf-8"))
            message["date"] = datetime.now()
            collection.insert_one(message)
            logging.info(
                f"Збережено повідомлення від {message.get('username', 'Unknown')}"
            )
    except Exception as e:
        logging.error(f"Помилка обробки повідомлення: {e}")
    finally:
        conn.close()


# HTTP Server Runner
def run_http_server():
    """Запускає HTTP-сервер."""
    server = ThreadedHTTPServer((HTTP_HOST, HTTP_PORT), SimpleHTTPRequestHandler)
    logging.info(f"HTTP-сервер запущено на {HTTP_HOST}:{HTTP_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    """Стартує HTTP та сокет-сервери у окремих процесах."""
    http_process = Process(target=run_http_server)
    socket_process = Process(target=run_socket_server)

    http_process.start()
    socket_process.start()

    http_process.join()
    socket_process.join()
