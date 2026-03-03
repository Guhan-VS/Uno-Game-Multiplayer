import json
import random
from channels.generic.websocket import AsyncWebsocketConsumer

rooms = {}

COLORS = ["Red", "Green", "Blue", "Yellow"]
VALUES = ["0","1","2","3","4","5","6","7","8","9","Skip","Reverse","+2"]

def create_deck():
    deck = []
    for color in COLORS:
        for value in VALUES:
            deck.append({"color": color, "value": value})
    for _ in range(5):
        deck.append({"color": "Wild", "value": "+4"})
    for _ in range(4):
        deck.append({"color": "Wild", "value": "Wild"})
    random.shuffle(deck)
    return deck


class GameConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room"]
        self.room_group_name = "game_" + self.room_name

        if self.room_name not in rooms:
            rooms[self.room_name] = {
                "players": {},
                "player_order": [],
                "deck": [],
                "player_cards": {},
                "table_card": None,
                "current_turn": 0,
                "direction": 1,
                "started": False,
                "pending_draw": 0,
                "host": None
            }

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        room = rooms[self.room_name]

        if self.channel_name in room["players"]:
            username = room["players"][self.channel_name]
            del room["players"][self.channel_name]
            if username in room["player_order"]:
                room["player_order"].remove(username)

        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        await self.broadcast_players()

    async def receive(self, text_data):
        data = json.loads(text_data)
        msg = data.get("type")
        room = rooms[self.room_name]

        if msg == "join":
            username = data["username"]
            if len(room["players"]) >= 10:
                return
            room["players"][self.channel_name] = username
            room["player_order"].append(username)
            if not room["host"]:
                room["host"] = username
            await self.broadcast_players()

        elif msg == "start":
            if data["username"] != room["host"]:
                return
            if len(room["player_order"]) < 2:
                return

            room["deck"] = create_deck()
            room["started"] = True
            room["direction"] = 1
            room["pending_draw"] = 0

            room["player_cards"] = {}

            for ch in room["players"]:
                room["player_cards"][ch] = []
                for _ in range(7):
                    room["player_cards"][ch].append(
                        room["deck"].pop()
                    )

            while True:
                first = room["deck"].pop()
                if first["color"] != "Wild" and first["value"] != "+2":
                    break
                room["deck"].insert(0, first)

            room["table_card"] = first
            room["current_turn"] = 0

            await self.broadcast_table()
            await self.broadcast_turn()
            await self.send_all_cards()

        elif msg == "play_card":
            await self.handle_play(data)

        elif msg == "draw":
            await self.handle_draw()

        elif msg == "choose_color":
            room["table_card"]["color"] = data["color"]
            await self.broadcast_table()
            await self.advance_turn()

        elif msg == "end_turn":
            await self.advance_turn()

    async def handle_play(self, data):
        room = rooms[self.room_name]
        username = room["players"][self.channel_name]
        card = data["card"]

        if room["player_order"][room["current_turn"]] != username:
            return

        if card not in room["player_cards"][self.channel_name]:
            return

        table = room["table_card"]

        # If stacking is active
        if room["pending_draw"] > 0:

            # Allow stacking only with +2 or +4
            if card["value"] not in ["+2", "+4"]:
                return

        else:
            # Normal validation
            valid = (
                card["color"] == table["color"] or
                card["value"] == table["value"] or
                card["color"] == "Wild"
            )
            if not valid:
                return

        room["player_cards"][self.channel_name].remove(card)
        room["table_card"] = card

        # Action logic
        if card["value"] == "+2":
            room["pending_draw"] += 2

        if card["value"] == "+4":
            room["pending_draw"] += 4

        if card["value"] == "Reverse":
            room["direction"] *= -1

        if card["value"] == "Skip":
            room["current_turn"] = (
                room["current_turn"] + room["direction"]
            ) % len(room["player_order"])

        await self.broadcast_table()
        await self.send_private_cards(self.channel_name)

        if len(room["player_cards"][self.channel_name]) == 0:
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "send_win", "player": username}
            )
            room["started"] = False
            return

        if card["color"] == "Wild":
            await self.send(text_data=json.dumps({
                "type": "choose_color"
            }))
            return

        await self.advance_turn()

    async def handle_draw(self):
        room = rooms[self.room_name]
        username = room["players"][self.channel_name]

        if room["player_order"][room["current_turn"]] != username:
            return

        # If stacked penalty exists
        if room["pending_draw"] > 0:

            amount = room["pending_draw"]

            for _ in range(amount):
                if room["deck"]:
                    room["player_cards"][self.channel_name].append(
                        room["deck"].pop()
                    )

            room["pending_draw"] = 0

            await self.send_private_cards(self.channel_name)
            await self.advance_turn()
            return

        # Normal draw
        drawn = room["deck"].pop()
        room["player_cards"][self.channel_name].append(drawn)

        await self.send_private_cards(self.channel_name)

        table = room["table_card"]

        playable = (
            drawn["color"] == table["color"] or
            drawn["value"] == table["value"] or
            drawn["color"] == "Wild"
        )

        if playable:
            await self.send(text_data=json.dumps({
                "type": "draw_play_option",
                "card": drawn
            }))
        else:
            await self.advance_turn()

    async def advance_turn(self):
        room = rooms[self.room_name]

        room["current_turn"] = (
            room["current_turn"] + room["direction"]
        ) % len(room["player_order"])

        await self.broadcast_turn()

    async def broadcast_players(self):
        room = rooms[self.room_name]
        players = []
        for ch, name in room["players"].items():
            players.append({
                "name": name,
                "is_host": name == room["host"],
                "ready": True
            })

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "send_players",
                "players": players,
                "count": len(players)
            }
        )

    async def broadcast_turn(self):
        room = rooms[self.room_name]
        player = room["player_order"][room["current_turn"]]
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "send_turn", "player": player}
        )

    async def broadcast_table(self):
        room = rooms[self.room_name]
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "send_table", "card": room["table_card"]}
        )

    async def send_all_cards(self):
        room = rooms[self.room_name]
        for ch in room["player_cards"]:
            await self.send_private_cards(ch)

    async def send_private_cards(self, channel):
        room = rooms[self.room_name]
        await self.channel_layer.send(
            channel,
            {
                "type": "send_private",
                "cards": room["player_cards"][channel]
            }
        )

    async def send_private(self, event):
        await self.send(text_data=json.dumps({
            "type": "your_cards",
            "cards": event["cards"]
        }))

    async def send_table(self, event):
        await self.send(text_data=json.dumps({
            "type": "table_card",
            "card": event["card"]
        }))

    async def send_turn(self, event):
        await self.send(text_data=json.dumps({
            "type": "turn",
            "player": event["player"]
        }))

    async def send_players(self, event):
        await self.send(text_data=json.dumps({
            "type": "players",
            "players": event["players"],
            "count": event["count"]
        }))

    async def send_win(self, event):
        await self.send(text_data=json.dumps({
            "type": "win",
            "player": event["player"]
        }))