pybabel extract --mapping-file=babel.cfg --output-file=locales/app.pot main.py src

pybabel update --input-file=locales/app.pot --output-dir=locales --domain=app

pybabel compile --directory=locales --domain=app
