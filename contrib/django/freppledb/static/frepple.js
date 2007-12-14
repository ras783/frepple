
var ContextMenu = {

	// private attributes
	_attachedElement : null,
	_menuElement : null,

  // A private hash mapping each class to a menu id
  _menus : {
    'buffer': 'buffercontext',
		'resource': 'resourcecontext',
		'operation': 'operationcontext',
		'location': 'locationcontext',
		'item': 'itemcontext',
    'demand': 'demandcontext',
		'forecast': 'forecastcontext',
		'customer': 'customercontext',
		'numfilteroper': 'datefilter',
    'datefilteroper': 'datefilter',
    'textfilteroper': 'textfilter'
	},

	// private method. Get which context menu to show
	_getMenuElementId : function (e)
	{
	  ContextMenu._attachedElement = Prototype.Browser.IE ?
	    event.srcElement :
	    e.target;

		while(ContextMenu._attachedElement != null)
		{
			var className = ContextMenu._attachedElement.className;

			if (typeof(className) != "undefined")
			{
				className = className.replace(/^\s+/g, "").replace(/\s+$/g, "")
				var classArray = className.split(/[ ]+/g);

				for (i = 0; i < classArray.length; i++)
					if (ContextMenu._menus[classArray[i]])
						return ContextMenu._menus[classArray[i]];
			}

		  ContextMenu._attachedElement = Prototype.Browser.IE ?
		    ContextMenu._attachedElement.parentElement :
		    ContextMenu._attachedElement.parentNode;
		}
		return null;
	},


	// private method. User clicked somewhere in the screen
	_onclick : function (e)
	{

		// Hide the previous context menu, and update operator
		if (ContextMenu._menuElement)
	  {
	    // Hide
			ContextMenu._menuElement.style.display = 'none';

			// If we are closing an operator menu, update the operator
			if (ContextMenu._menuElement.hasClassName("OperatorMenu") && ContextMenu._attachedElement)
			{
			  // Operator selected, or clicked somewhere else?
			  x = Prototype.Browser.IE ? event.srcElement : e.target;
        if ($(x).up().hasClassName("OperatorMenu"))
        {
			    // Update the span displaying the choosen operator
			    ContextMenu._attachedElement.innerHTML = x.innerHTML;

			    // Update the name of the filter input field
			    filterfield = ContextMenu._attachedElement.id.replace("operator","filter");
          $(filterfield).name = $(filterfield).name.substr(0,$(filterfield).name.lastIndexOf("__")+2) + x.id;
        }
		  }
		}

    // No further handling for rightclicks
    if (e!=undefined && !Event.isLeftClick(e)) return true;

    // Find the id of the menu to display
		var menuElementId = ContextMenu._getMenuElementId(e);
		if (menuElementId)
		{
			var m = ContextMenu._getMousePosition(e);
			var s = ContextMenu._getScrollPosition(e);

			ContextMenu._menuElement = $(menuElementId);

      // Get the entity name
			var item = ContextMenu._attachedElement.innerHTML;

			// Unescape all escaped characters and urlencode the result for usage as a url
			item = encodeURIComponent(item.replace(/&amp;/g,'&').replace(/&lt;/g,'<')
			  .replace(/&gt;/g,'>').replace(/&#39;/g,"'").replace(/&quot;/g,'"'));

			// Build the urls for the menu
			var l = ContextMenu._menuElement.getElementsByTagName("a");
			for (x=0; x<l.length; x++)
			  l[x].href = l[x].id.replace(/%s/,item);

      // Display the menu at the right location
			ContextMenu._menuElement.style.left = m.x + s.x + 'px';
			ContextMenu._menuElement.style.top = m.y + s.y + 'px';
			ContextMenu._menuElement.style.display = 'block';
			return false;
		}

		var returnValue = true;
		var evt = Prototype.Browser.IE ? window.event : e;

		if (evt.button != 1)
		{
			if (evt.target) var el = evt.target;
			else if (evt.srcElement) var el = evt.srcElement;
			var tname = el.tagName.toLowerCase();
			if ((tname == "input" || tname == "textarea")) return true;
		}
	  else
		  return  false;
	},


	// private method. Returns mouse position
	_getMousePosition : function (e)
	{
		e = e ? e : window.event;
		return {'x' : e.clientX, 'y' : e.clientY}
	},


	// private method. Get document scroll position
	_getScrollPosition : function ()
	{
		if( typeof( window.pageYOffset ) == 'number' )
		  return {'x' : window.pageXOffset, 'y' : window.pageYOffset}
		else if( document.documentElement && ( document.documentElement.scrollLeft || document.documentElement.scrollTop ) )
		  return {'x' : document.documentElement.scrollLeft, 'y' : document.documentElement.scrollTop}
		else if( document.body && ( document.body.scrollLeft || document.body.scrollTop ) )
		  return {'x' : document.body.scrollLeft, 'y' : document.body.scrollTop}
		return {'x' : 0, 'y' : 0}
	}

}

// Install a handler for all clicks on the page
document.onclick = ContextMenu._onclick;


//----------------------------------------------------------------------------
// Code for handling the menu bar and active button.
//----------------------------------------------------------------------------

var activeButton = null;

// Capture mouse clicks on the page so any active button can be deactivated.
document.observe('mousedown',  function (event) {

  // If there is no active button, exit.
  if (activeButton == null) return;

  // Find the element that was clicked on.
  var el = Event.element(event);

  // If the active button was clicked on, exit.
  if (el == activeButton) return;

  // If the element is not part of a menu, reset and clear the active button.
  if (el.up('div.menu') == undefined) {
    resetButton(activeButton);
    activeButton = null;
  }
}, true);


function buttonClick(event, menuId)
{

  // Get the target button element.
  var button = $(Event.element(event));

  // Blur focus from the link to remove that annoying outline.
  button.blur();

  // Associate the named menu to this button if not already done.
  // Additionally, initialize menu display.
  if (button.menu == null) button.menu = $(menuId);

  // Reset the currently active button, if any.
  if (activeButton != null) resetButton(activeButton);

  // Activate this button, unless it was the currently active one.
  if (button != activeButton)
  {
    // Update the button's style class to make it look like it's depressed.
    button.addClassName("menuButtonActive");

    // Position the associated drop down menu under the button and show it.
    var pos = button.cumulativeOffset();
    pos[1] += button.getHeight();
    button.menu.style.left = pos[0] + "px";
    button.menu.style.top  = pos[1] + "px";

    button.menu.style.visibility = "visible";
    activeButton = button;
  }
  else
    activeButton = null;

  return false;
}


function buttonMouseover(event, menuId)
{
  // If any other button menu is active, make this one active instead.
  if (activeButton != null && activeButton != $(Event.element(event)))
    buttonClick(event, menuId);
}


function resetButton(button)
{
  // Restore the button's style class.
  button.removeClassName("menuButtonActive");

  // Hide the button's menu
  if (button.menu != null) button.menu.style.visibility = "hidden";
}
