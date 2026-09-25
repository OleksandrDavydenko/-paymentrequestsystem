import os

from dotenv import load_dotenv
from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
import identity.flask

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["FLASK_SECRET_KEY"],
    SESSION_TYPE="filesystem",  # сесії зберігаються на сервері, не в cookie
    SESSION_PERMANENT=False,
)
# App Service приймає HTTPS на проксі, а до Flask передає HTTP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Single-tenant authority: увійти можуть лише користувачі нашого тенанту Entra ID.
auth = identity.flask.Auth(
    app,
    authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}",
    client_id=os.environ["CLIENT_ID"],
    client_credential=os.environ["CLIENT_SECRET"],
    redirect_uri=os.environ["REDIRECT_URI"],  # напр. http://localhost:5000/getAToken
)


@app.route("/")
@auth.login_required
def index(*, context):
    user = context["user"]
    return render_template(
        "index.html",
        name=user.get("name"),
        email=user.get("preferred_username"),
        oid=user.get("oid"),
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(port=5000, debug=True)
