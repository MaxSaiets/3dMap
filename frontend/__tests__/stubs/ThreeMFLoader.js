class ThreeMFLoader {
  load(_url, _onLoad, _onProgress, onError) {
    // We don't use 3MF in unit tests; fail fast if accidentally called
    if (onError) onError(new Error('ThreeMFLoader stub: not implemented in unit tests'))
  }

  parse(_data, _path, _onLoad, onError) {
    if (onError) onError(new Error('ThreeMFLoader stub: parse not implemented'))
  }
}

module.exports = { ThreeMFLoader }


