# source=https://github.com/superponible/DFIR/
# license=MIT

sift-scripts-parseusn:
  file.recurse:
    - name: /usr/local/bin/
    - source: sallt://sift/files/parseusn
    - file_mode: 755
    - include_pat: '*.py'
