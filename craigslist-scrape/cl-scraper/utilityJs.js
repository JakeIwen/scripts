var numClicks = 0;
function clickChildren(el){
	console.log('el', el);
	el.trigger("click");
	el.click();
	numClicks++;
	var children = el.children();
	children.length && el.children().each( function() {
        clickChildren($(this));
	})
}
clickChildren($("#Button_23").parent().parent().parent())
//chickChildren($("div#autoplayDiv"));
numClicks;
//start!
$("#playImage").click();
