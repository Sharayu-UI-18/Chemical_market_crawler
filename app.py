from flask import Flask, render_template, request, jsonify

from aggregator import check_market_availability
from utils.pubchem import get_pubchem_properties

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():

    data = request.get_json()

    cas = data.get("cas", "").strip()

    if not cas:
        return jsonify({
            "success": False,
            "message": "Please enter a CAS number."
        })

    # -----------------------------
    # Supplier Availability
    # -----------------------------
    result = check_market_availability(cas)

    # -----------------------------
    # PubChem Data
    # -----------------------------
    pubchem = get_pubchem_properties(cas)

    # Add PubChem data to result
    result["pubchem"] = pubchem

    return jsonify({
        "success": True,
        "result": result
    })


if __name__ == "__main__":
    app.run(debug=True)