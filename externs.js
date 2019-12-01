/** @type{?function()} */
var puzzle_init;

/** @type{number} */
var wid;

/** @type{Storage} */
var localStorage;

class Message {
    constructor() {
	/** @type{string} */
	this.method;
	/** @type{string} */
	this.text;
	/** @type{string} */
	this.alt;
	/** @type{Array<number>} */
	this.wids;
    }
}
