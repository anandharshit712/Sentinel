"""Flask HTTP surface wiring auth + payments."""
from flask import Flask, jsonify, request

from app.auth.login import AuthError, authenticate
from app.payments.processor import PaymentError, process_payment

app = Flask(__name__)


@app.get("/health")
def health_route():
    return jsonify({"status": "ok"})


@app.post("/login")
def login_route():
    data = request.get_json(force=True)
    try:
        user_id = authenticate(data["username"], data["password"])
    except AuthError:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"user_id": user_id})


@app.post("/pay")
def pay_route():
    data = request.get_json(force=True)
    try:
        pid = process_payment(data["username"], data["password"], data["amount"])
    except (AuthError, PaymentError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"payment_id": pid})
