from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("upload.html")

@app.route("/upload", methods=['POST'])
def upload():
    filename = None
    if request.method == "POST":

        file = request.files['file']
        filename = file.filename
        print(filename)
        print(file)
        destination = "/".join(["target: ", filename])
        print(destination)
        # file.save(destination)

    return render_template("complete.html", filename=filename)

def run_matching(filename):
    print(filename)

if __name__ == "__main__":
    app.run(debug=True)
