import { createReceiverClient } from './receiver-client.mjs';
import { createMessageHandler, installCommands, installContextMenus } from './background-actions.mjs';

const receiver = createReceiverClient();
browser.runtime.onMessage.addListener(createMessageHandler(browser, receiver));
installCommands(browser, receiver);
void installContextMenus(browser, receiver).catch(error => {
  console.error('Van TV context menu setup failed', error);
});
