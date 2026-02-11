import json
import os
import shutil


class Room:
    def __init__(self, orc_dir, name):
        self.orc_dir = orc_dir
        self.name = name
        self.path = os.path.join(orc_dir, name)

    def exists(self):
        return os.path.isdir(self.path) and os.path.isfile(
            os.path.join(self.path, "agent.json")
        )

    def create(self, role="worker", status="idle"):
        os.makedirs(self.path, exist_ok=True)
        os.makedirs(os.path.join(self.path, "molecules"), exist_ok=True)

        self._write_json("agent.json", {"role": role, "sessions": []})
        self._write_json("status.json", {"status": status})
        self._write_json("inbox.json", [])

    def delete(self):
        if os.path.isdir(self.path):
            shutil.rmtree(self.path)

    def read_agent(self):
        return self._read_json("agent.json")

    def read_status(self):
        return self._read_json("status.json")

    def set_status(self, status):
        self._write_json("status.json", {"status": status})

    def read_inbox(self):
        return self._read_json("inbox.json")

    def _read_json(self, filename):
        path = os.path.join(self.path, filename)
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def _write_json(self, filename, data):
        path = os.path.join(self.path, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
