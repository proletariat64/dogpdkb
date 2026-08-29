i have scripts/paddleocr_convert.py use paddle ocr api to convert pictures inside a markdown.

now i want to upgrade it into a cli 'mdocr'

it can

- ocr markdown inside pictue into markdown, and put it converted contents at its original position.
- there is a remark like 'following is ocr result by mdocr ...  end of ocr result'
- at the end of new markdown a line quote the pic file link.
- if the pic isn't local download it. all pic coverted to jpg format for local storage. don't build wheel of jpg convert, we have local pillow or other cli can do it. this is a markdown prepare, and download pic files can be on/off if off just skip online pic links dont convert.
- use paddle ocr to convert, a good implemenation can be found at scripts/paddleocr_convert.py
- mdocr no args display mdocr -h/--help by default.
- mdocr ocr -f/--file filename1, filename2, ...
  mdocr prepare -f/--file ...
