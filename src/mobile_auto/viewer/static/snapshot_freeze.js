/* 编辑底图冻结引用的状态机(浏览器与 node 单测共用)。
 *
 * 不变量:任意时刻至多一份冻结引用。冻结快照不参与快照库的容量驱逐,
 * 只增不放会把实时配额挤满,新快照一创建就被驱逐(冻结与读取 404),
 * 因此换底图必须先释放旧引用再冻结新的;回到实时/切换设备时释放。
 * 释放尽力而为(失败由冻结超时兜底);冻结失败向调用方抛出,由界面
 * 提示用户底图将按实时有效期过期。
 */
"use strict";

(function (root) {
  function createSnapshotFreeze(post) {
    let frozenId = null;

    return {
      get current() {
        return frozenId;
      },

      /* 固定新的编辑底图:先释放旧引用,冻结失败时不登记(保持未冻结)。
         返回 promise;拒绝时 frozenId 已复位为 null。 */
      async freeze(id) {
        const previous = frozenId;
        frozenId = null;
        if (previous && previous !== id) {
          await post(previous, false).catch(() => {});
        }
        if (!id) return null;
        await post(id, true);
        frozenId = id;
        return id;
      },

      /* 放弃当前冻结引用(回到实时/切换设备/页面卸载);失败不抛出。 */
      release() {
        const previous = frozenId;
        frozenId = null;
        if (!previous) return Promise.resolve();
        return post(previous, false).catch(() => {});
      },
    };
  }

  const api = { createSnapshotFreeze };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.SnapshotFreeze = api;
})(typeof window !== "undefined" ? window : null);
