// Shared by pure tests and the opt-in stored-action smoke.
module.exports = [
    ['lower', '  HéLLO_There!\r\n\tWORLD 😀', '  héllo_there!\r\n\tworld 😀'],
    ['upper', '  Hello_there!\n\tß 😀', '  HELLO_THERE!\n\tSS 😀'],
    ['snake', 'Hello World\r\nNext Line', 'hello_world\r\nnext_line'],
    ['kebab', 'Hello World\nNext Line', 'hello-world\nnext-line'],
    ['camel', 'Hello World\nNext Line', 'helloWorld\nnextLine'],
    ['base64-encode', 'Héllo 😀', Buffer.from('Héllo 😀').toString('base64')],
    ['base64-decode', Buffer.from('Héllo 😀').toString('base64'), 'Héllo 😀'],
    ['uri-encode', 'https://example.com/a b?q=é', 'https://example.com/a%20b?q=%C3%A9'],
    ['uri-decode', 'https://example.com/a%20b?q=%C3%A9', 'https://example.com/a b?q=é']
];
