from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def player():
    return render_template("player.html")

if __name__ == "__main__":
    app.run(debug=True)