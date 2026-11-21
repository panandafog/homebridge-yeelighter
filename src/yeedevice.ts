/**
 * Yeelight Device Handling.
 */

import { EventEmitter } from "node:events";
import net from "node:net";

export interface DeviceInfo {
  location: string;
  id: string;
  model: string;
  support: string;
  power: boolean;
  bright: number;
  color_mode: number;
  ct: number;
  rgb: string;
  hue: number;
  sat: number;
  host: string;
  port: number;
  debug: boolean;
  trackedAttributes: string[];
  fw_ver: string;
  name: string;
}

export const EMPTY_DEVICEINFO: DeviceInfo = {
  location: "",
  id: "",
  model: "string",
  support: "string",
  power: false,
  bright: 0,
  color_mode: -1,
  ct: 0,
  rgb: "string",
  hue: 0,
  sat: 0,
  host: "string",
  port: 0,
  debug: false,
  trackedAttributes: [],
  fw_ver: "0.0.0",
  name: "string"
};

export interface Command {
  id: number;
  method: string;
  params: Array<number | string | boolean>;
}

/**
 * Handles the connection to a concrete Yee light.
 */
export class Device extends EventEmitter {
  info: DeviceInfo;
  debug: boolean;
  connected: boolean;
  forceDisconnect: boolean;
  retryTimer?: NodeJS.Timeout;
  socket?: net.Socket;
  rest = "";
  constructor(info: DeviceInfo) {
    super();
    this.info = info;
    this.debug = this.info.debug || false;
    this.connected = false;
    this.forceDisconnect = false;
  }

  reconnect() {
    try {
      this.forceDisconnect = false;
      if (this.retryTimer) {
        clearTimeout(this.retryTimer);
        delete this.retryTimer;
      }
      if (this.socket && !this.socket.destroyed && !this.socket.closed && !this.socket.errored) {
        return;
      }
      if (this.socket) {
        this.socket.destroy();
      }
      this.rest = "";
      const socket = new net.Socket({ allowHalfOpen: false });
      this.socket = socket;
      this.bindSocket();
      socket.connect({ host: this.info.host, port: this.info.port }, () => {
        if (this.socket !== socket || this.forceDisconnect) {
          return;
        }
        this.didConnect();
        this.emit("connected");
      });
    } catch (error: any) {
      this.socketClosed(error);
    }
  }

  connect() {
    this.reconnect();
  }

  disconnect(forceDisconnect = true) {
    this.forceDisconnect = forceDisconnect;
    this.connected = false;
    const socket = this.socket;
    delete this.socket;
    this.rest = "";
    if (socket) {
      socket.destroy();
    }
    this.emit("disconnected");
    if (this.forceDisconnect && this.retryTimer) {
      clearTimeout(this.retryTimer);
      delete this.retryTimer;
    }
  }

  bindSocket() {
    const socket = this.socket;
    if (!socket) {
      return;
    }
    socket.on("data", (data) => {
      if (this.socket === socket && !this.forceDisconnect) {
        this.didReceiveResponse(data);
      }
    });

    socket.on("error", (error) => {
      if (this.socket === socket && !this.forceDisconnect) {
        this.emit("socketError", error);
        this.socketClosed(error);
      }
    });

    socket.on("end", () => {
      if (this.socket === socket && !this.forceDisconnect) {
        this.emit("socketEnd");
        this.socketClosed();
      }
    });

    socket.on("close", () => {
      if (this.socket === socket && !this.forceDisconnect) {
        this.socketClosed();
      }
    });
  }

  socketClosed(error?: Error) {
    if (this.forceDisconnect || (!this.socket && this.retryTimer)) {
      return;
    }

    if (error) {
      console.log("Socket closed; retrying to connect in 5s:", error.message);
    } else {
      console.log("Socket closed by peer; retrying to connect in 5s");
    }
    this.disconnect(false);
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      delete this.retryTimer;
    }
    this.retryTimer = setTimeout(() => {
      delete this.retryTimer;
      if (!this.forceDisconnect) {
        this.reconnect();
      }
    }, 5000);
  }

  didConnect() {
    this.connected = true;
  }

  didReceiveResponse(data) {
    const combined = this.rest + data.toString("utf8");
    const dataArray = combined.split("\r\n");
    this.rest = dataArray.pop() || "";
    for (const dataString of dataArray) {
      if (dataString.length === 0) {
        continue;
      }
      try {
        const response = JSON.parse(dataString);
        if (response.id) {
          this.emit("deviceUpdate", response);
        } else {
          // invalid message (disabled logging since this seems to happen regularly for some lights)
        }
      } catch (error) {
        console.error(error, dataString);
      }
    }
  }

  sendCommand(data) {
    const cmd = JSON.stringify(data);
    if (this.connected && this.socket) {
      try {
        this.socket.write(cmd + "\r\n");
      } catch (error: any) {
        this.socketClosed(error);
      }
    }
  }

  updateDevice(device) {
    this.info = device;
  }
}
