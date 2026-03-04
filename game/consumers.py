import json
import random
from channels.generic.websocket import AsyncWebsocketConsumer

rooms = {}

colors = ["Red","Blue","Green","Yellow"]


def create_deck():

    deck = []

    for color in colors:

        for i in range(10):
            deck.append({"color":color,"value":str(i)})

        deck.append({"color":color,"value":"+2"})
        deck.append({"color":color,"value":"Skip"})
        deck.append({"color":color,"value":"Reverse"})

    for _ in range(4):
        deck.append({"color":"Wild","value":"+4"})
        deck.append({"color":"Wild","value":"Wild"})

    random.shuffle(deck)

    return deck


class GameConsumer(AsyncWebsocketConsumer):

    async def connect(self):

        self.room = self.scope["url_route"]["kwargs"]["room"]
        self.group = f"room_{self.room}"

        await self.channel_layer.group_add(self.group,self.channel_name)
        await self.accept()


    async def disconnect(self,close_code):

        if self.room in rooms:

            rooms[self.room]["players"] = [
                p for p in rooms[self.room]["players"]
                if p["channel"] != self.channel_name
            ]

            await self.broadcast_players()

        await self.channel_layer.group_discard(self.group,self.channel_name)


    async def receive(self,text_data):

        data=json.loads(text_data)

        if data["type"]=="join":
            await self.join_player(data)

        if data["type"]=="start":
            await self.start_game()

        if data["type"]=="play":
            await self.play_card(data)

        if data["type"]=="draw":
            await self.draw_card()


    async def join_player(self,data):

        username=data["username"]

        if self.room not in rooms:

            rooms[self.room]={
                "players":[],
                "deck":[],
                "table":None,
                "turn":0,
                "direction":1,
                "pending_draw":0,
                "started":False
            }

        rooms[self.room]["players"].append({
            "username":username,
            "channel":self.channel_name,
            "hand":[]
        })

        await self.broadcast_players()


    async def broadcast_players(self):

        players=[p["username"] for p in rooms[self.room]["players"]]

        await self.channel_layer.group_send(
            self.group,
            {
                "type":"players_update",
                "players":players
            }
        )


    async def start_game(self):

        room=rooms[self.room]

        room["deck"]=create_deck()

        players=room["players"]

        for p in players:
            p["hand"]=[room["deck"].pop() for _ in range(7)]

        table=room["deck"].pop()

        while table["value"] in ["+4","+2","Wild"]:
            room["deck"].insert(0,table)
            table=room["deck"].pop()

        room["table"]=table

        room["turn"]=0
        room["pending_draw"]=0
        room["direction"]=1
        room["started"]=True

        await self.broadcast_state()


    async def broadcast_state(self):

        room=rooms[self.room]
        players=room["players"]

        current=players[room["turn"]]["username"]

        for p in players:

            await self.channel_layer.send(
                p["channel"],
                {
                    "type":"game_state",
                    "hand":p["hand"],
                    "table":room["table"],
                    "turn":current,
                    "pending":room["pending_draw"]
                }
            )


    async def game_state(self,event):

        await self.send(text_data=json.dumps({
            "type":"state",
            "hand":event["hand"],
            "table":event["table"],
            "turn":event["turn"],
            "pending":event["pending"]
        }))


    async def play_card(self,data):

        room=rooms[self.room]
        players=room["players"]

        player=players[room["turn"]]

        if player["channel"]!=self.channel_name:
            return

        card=data["card"]

        if card not in player["hand"]:
            return

        table=room["table"]

        valid=(
            card["color"]==table["color"]
            or card["value"]==table["value"]
            or card["color"]=="Wild"
        )

        if not valid:
            return


        if room["pending_draw"]>0:

            if card["value"]=="+2":
                room["pending_draw"]+=2

            elif card["value"]=="+4" and room["pending_draw"]>=4:
                room["pending_draw"]+=4

            else:
                return


        player["hand"].remove(card)

        if card["color"]=="Wild" and "color" in data:
            room["table"]={"color":data["color"],"value":card["value"]}
        else:
            room["table"]=card


        if card["value"]=="+2":
            room["pending_draw"]+=2

        if card["value"]=="+4":
            room["pending_draw"]+=4


        if card["value"]=="Reverse":
            room["direction"]*=-1


        if card["value"]=="Skip":

            room["turn"]=(room["turn"]+room["direction"])%len(players)


        room["turn"]=(room["turn"]+room["direction"])%len(players)


        await self.check_loser()

        await self.broadcast_state()


    async def draw_card(self):

        room=rooms[self.room]
        players=room["players"]

        player=players[room["turn"]]

        if player["channel"]!=self.channel_name:
            return


        draw=room["pending_draw"] if room["pending_draw"]>0 else 1

        for _ in range(draw):

            if len(room["deck"])==0:
                room["deck"]=create_deck()

            player["hand"].append(room["deck"].pop())


        room["pending_draw"]=0

        room["turn"]=(room["turn"]+room["direction"])%len(players)

        await self.broadcast_state()


    async def check_loser(self):

        room=rooms[self.room]

        remaining=[p for p in room["players"] if len(p["hand"])>0]

        if len(remaining)==1:

            loser=remaining[0]["username"]

            await self.channel_layer.group_send(
                self.group,
                {
                    "type":"game_over",
                    "loser":loser
                }
            )


    async def game_over(self,event):

        await self.send(text_data=json.dumps({
            "type":"game_over",
            "loser":event["loser"]
        }))


    async def players_update(self,event):

        await self.send(text_data=json.dumps({
            "type":"players_update",
            "players":event["players"]
        }))