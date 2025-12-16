class STLLoader {
  load(_url, onLoad, _onProgress, _onError) {
    // Minimal geometry stub that Three.js Mesh can accept in tests
    onLoad({
      attributes: {
        position: { count: 0 },
      },
    })
  }
}

module.exports = { STLLoader }


