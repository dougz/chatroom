goog.require('goog.dom');
goog.require('goog.dom.classlist');
goog.require('goog.dom.TagName');
goog.require('goog.events');
goog.require('goog.events.KeyCodes');
goog.require('goog.net.XhrIo');
goog.require("goog.json.Serializer");

class ChatroomDispatcher {
    constructor() {
	this.methods = {
	    "add_chat": goog.bind(this.add_chat, this),
	}
    }

    /** @param{Message} msg */
    dispatch(msg) {
	this.methods[msg.method](msg);
    }

    /** @param{Message} msg */
    add_chat(msg) {
	var curr = goog.dom.getChildren(chatroom.chat);
	if (curr.length > 20) {
	    goog.dom.removeNode(curr[0]);
	}

        var text;
        if (!msg.wids) {
            text = msg.text;
        } else {
            if (msg.wids.includes(wid)) {
                text = msg.text;
            } else {
                text = msg.alt;
            }
        }
	var el = goog.dom.createDom("P", null,
                                goog.dom.createDom("B", null, msg.who),
                                ": ", text);
	chatroom.chat.appendChild(el);
    }
}

function chatroom_submit(textel, e) {
    var text = textel.value;
    if (text == "") return;
    textel.value = "";
    var msg = chatroom.serializer.serialize({"text": text});
    goog.net.XhrIo.send("/chatsubmit", Common_expect_204, "POST", msg);
    e.preventDefault();
}

function chatroom_onkeydown(textel, e) {
    if (e.keyCode == goog.events.KeyCodes.ENTER) {
	chatroom_submit(textel, e);
    }
}

var chatroom = {
    waiter: null,
    entry: null,
    chat: null,
}

puzzle_init = function() {
    chatroom.serializer = new goog.json.Serializer();

    chatroom.body = goog.dom.getElement("puzz");
    chatroom.entry = goog.dom.getElement("entry");
    chatroom.text = goog.dom.getElement("text");
    chatroom.chat = goog.dom.getElement("chat");

    goog.events.listen(goog.dom.getElement("text"),
		       goog.events.EventType.KEYDOWN,
		       goog.bind(chatroom_onkeydown, null, chatroom.text));
    goog.events.listen(goog.dom.getElement("chatsubmit"),
		       goog.events.EventType.CLICK,
                       goog.bind(chatroom_submit, null, chatroom.text));

    chatroom.waiter = new Common_Waiter(new ChatroomDispatcher(), "/chatwait", 0, null, null);
    chatroom.waiter.start();
}

