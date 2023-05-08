//  Example request to NSO JSONRPC

// jsonrpc request counter, each request should have an unique id.
let id = 0;

// Return JSON representation of method and param
const newRequest = (method, params) => {
    id += 1;
    return JSON.stringify({ jsonrpc: '2.0', id, method, params });
};

// JSON-RPC helper
// Return request promise 
const jsonrpc = (method, params) => {
    const url = `/jsonrpc/${method}`;
    const body = newRequest(method, params);

    return fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            Accept: 'application/json;charset=utf-8',
            'Content-Type': 'application/json;charset=utf-8',
        },
        body,
    }).then((response) => {
        if (response.status !== 200) {
            return Promise.reject(new Error(method, response, body));
        }
        return response.json();
    }).then((json) => {
        if (json.error) {
            return Promise.reject(new Error(json.error, body));
        }
        return json.result;
    });
};

function getElmHeight(node) {
    const list = [
        'margin-top',
        'margin-bottom',
        'border-top',
        'border-bottom',
        'padding-top',
        'padding-bottom',
        'height'
    ]

    const style = window.getComputedStyle(node)
    return list
        .map(k => parseInt(style.getPropertyValue(k), 10))
        .reduce((prev, cur) => prev + cur)
}

const start_matrix = () => {
    // Get the canvas node and the drawing context
    const canvas = document.getElementById('thematrix');
    const ctx = canvas.getContext('2d');

    // set the width and height of the canvas
//    const headerHeight = document.getElementById('headerTitle').offsetHeight;
//    const titleHeight = getElmHeight(document.getElementById('matrix'));
    const w = canvas.width = document.body.offsetWidth-65;
//    const h = canvas.height = window.innerHeight-headerHeight-titleHeight-65;
    const h = canvas.height = window.innerHeight-65;

    const theend = document.getElementById('theend')
    const theendtext = document.getElementById('theendtext')
    theendtext.style.fontSize = h/20 + "px";
    theend.style.left = (w/2-theend.offsetWidth/2) + "px";
    theend.style.top = (h/2-theendtext.offsetHeight/2) + "px";

    // draw a black rectangle of width and height same as that of the canvas
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);

    const cols = Math.floor(w / 20) + 1;
    const ypos = Array(cols).fill(0);

    function matrix () {
      // Draw a semitransparent black rectangle on top of previous drawing
      ctx.fillStyle = '#0001';
      ctx.fillRect(0, 0, w, h);

      // Set color to green and font to 15pt monospace in the drawing context
      ctx.fillStyle = '#0f0';
      //ctx.font = '15pt monospace';
      ctx.font = '15pt Apple SD Gothic Neo';

      // for each column put a random character at the end
      ypos.forEach((y, ind) => {
        // generate a random character
        const text = String.fromCharCode(Math.random() * 128 + 0x3041);

        // x coordinate of the column, y coordinate is already given
        const x = ind * 20;
        // render the character at (x, y)
        ctx.fillText(text, x, y);

        // randomly reset the end of the column if it's at least 100px high
        if (y > 100 + Math.random() * 10000) ypos[ind] = 0;
        // otherwise just move the y coordinate for the column 20px down,
        else ypos[ind] = y + 20;
      });
    }

    // render the animation at 20 FPS.
    setInterval(matrix, 50);
}

// fetch the system version with the get_system_setting method.
// Return request promise
const fetchSystemVersion = () => jsonrpc('get_system_setting', { operation: 'version' });

// update the systemVersion element when the window has loaded.
window.addEventListener('load', () => {
    fetchSystemVersion().then((version) => {
//        const element = document.getElementById('headerTitle');
//        const headerText = ['System version', '' + version,  'Matrix manager'].join(' ');
//        element.innerText = headerText;
        start_matrix();
    });
});
