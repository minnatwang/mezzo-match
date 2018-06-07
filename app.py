from flask import Flask
from flask import render_template
from flask import request

app = Flask(__name__)

@app.route("/", methods=['GET', 'POST'])
def application():
    # DEMO CODE
    # answer = "this is just a string!" # logic("example input")
    # return answer

    # NEW CODE
    answer = None
    if request.method == "POST":
        raw_input = request.form["input"]
        # check_input_for_errors(raw_input)
        answer = raw_input # logic(raw_input)
    return render_template('index.html', answer=answer)

if __name__ == "__main__":
    app.run()
