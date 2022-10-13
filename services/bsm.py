import requests
import msgpack
import tarfile
from io import BytesIO
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import fitz

service = "bsm"

key = bytes([30, 0, 184, 152, 115, 19, 157, 33, 4, 237, 80, 26, 139, 248, 104, 155])

def getlogindata(username, password):
	r = requests.post("https://www.bsmart.it/api/v5/session", data={"password": password, "email": username})
	return r.json()

def getlibrary(token):
	r = requests.get("https://www.bsmart.it/api/v5/books", headers={"AUTH_TOKEN": token}, params={"per_page": 1000000, "page_thumb_size": "medium"})
	return r.json()

def getbookinfo(token, bookid, revision, operation):
	r = requests.get("https://www.bsmart.it/api/v5/books/" + str(bookid) + "/" + str(revision) + "/" + operation, headers={"AUTH_TOKEN": token}, params={"per_page": 1000000})
	open(f"{bookid}-{operation}.json", "wb").write(r.content)
	return r.json()

def downloadpack(url, progress, total, done):
	r = requests.get(url, stream=True)
	length = int(r.headers.get("content-length", 1))
	file = b""
	for data in r.iter_content(chunk_size=102400):
		file += data
		progress(round(done + len(file) / length * total))
	return tarfile.open(fileobj=BytesIO(file))

def cover(token, bookid, data):
	r = requests.get(data["cover"])
	return r.content

def decryptfile(file):
	header = msgpack.unpackb(file.read(256).rstrip(b"\x00"))

	iv = file.read(16)
	obj = AES.new(key, AES.MODE_CBC, iv)
	dec = obj.decrypt(file.read(header["start"] - 256 - 16))

	return unpad(dec, AES.block_size) + file.read(), header["md5"]

def login(username, password):
	logindata = getlogindata(username, password)
	if "auth_token" not in logindata:
		print("Login failed: " + logindata["message"])
	else:
		return logindata["auth_token"]

def checktoken(token):
	test = getlibrary(token)
	return "message" not in test

def library(token):
	books = dict()
	for i in getlibrary(token):
		books[str(i["id"])] = {"title": i["title"], "revision": i["current_edition"]["revision"], "cover": i["cover"]}

	return books

def downloadbook(token, bookid, data, progress):
	revision = data["revision"]
	progress(0, "Getting resources")
	resources = getbookinfo(token, bookid, revision, "resources")
	resmd5 = {next(j["md5"] for j in i["assets"] if j["use"] == "page_pdf"): i["id"] for i in resources if i["resource_type_id"] == 14}

	pagespdf = dict()
	progress(5, "Fetching asset packs")
	assetpacks = getbookinfo(token, bookid, revision, "asset_packs")

	progress(10, "Downloading pdf pages")
	pagespack = downloadpack(next(i["url"] for i in assetpacks if i["label"] == "page_pdf"), progress, 80, 10)

	progress(90, "Decrypting pages")
	for member in pagespack.getmembers():
		file = pagespack.extractfile(member)
		if file:
			output, md5 = decryptfile(file)
			pagespdf[resmd5[md5]] = output

	pdf = fitz.Document()
	toc = []

	progress(95, "Obtaining toc")
	index = getbookinfo(token, bookid, revision, "index")

	bookmarks = {i["first_page"]["id"]:i["title"] for i in index}
	for i, (pageid, pagepdfraw) in enumerate(sorted(pagespdf.items())):
		pagepdf = fitz.Document(stream=pagepdfraw, filetype="pdf")
		pdf.insert_pdf(pagepdf)
		if pageid in bookmarks:
			toc.append([1, bookmarks[pageid], i + 1])

	progress(98, "Applying toc")
	pdf.set_toc(toc)
	return pdf